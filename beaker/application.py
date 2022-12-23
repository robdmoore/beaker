import base64
from inspect import getattr_static
from typing import Final, Any, cast, Optional
from algosdk.v2client.algod import AlgodClient
from algosdk.abi import Method
from pyteal import (
    SubroutineFnWrapper,
    TealInputError,
    Txn,
    MAX_TEAL_VERSION,
    ABIReturnSubroutine,
    BareCallActions,
    Expr,
    Global,
    OnCompleteAction,
    OptimizeOptions,
    Router,
    Approve,
)

from beaker.decorators import (
    get_handler_config,
    MethodHints,
    MethodConfig,
    create,
    HandlerFunc,
)

from beaker.state import (
    AccountState,
    AccountStateBlob,
    ApplicationStateBlob,
    ApplicationState,
    ReservedAccountStateValue,
    AccountStateValue,
    ApplicationStateValue,
    ReservedApplicationStateValue,
)
from beaker.errors import BareOverwriteError
from beaker.precompile import AppPrecompile, LSigPrecompile


def get_method_spec(fn: HandlerFunc) -> Method:
    hc = get_handler_config(fn)
    if hc.method_spec is None:
        raise Exception("Expected argument to be an ABI method")
    return hc.method_spec


def get_method_signature(fn: HandlerFunc) -> str:
    return get_method_spec(fn).get_signature()


def get_method_selector(fn: HandlerFunc) -> bytes:
    return get_method_spec(fn).get_selector()


class ApplicationMeta(type):
    def __new__(mcs, name, bases, dct):
        collect_keys = ["_acct_vals", "_app_vals", "_precompiles"]
        for key in collect_keys:
            dct[key] = {}
        for base in bases:
            if issubclass(base, Application):
                for key in collect_keys:
                    dct[key].update(getattr(base, key, {}))
        cls = super().__new__(mcs, name, bases, dct)
        return cls


class Application(metaclass=ApplicationMeta):
    """Application contains logic to detect State Variables, Bare methods
    ABI Methods and internal subroutines.

    It should be subclassed to provide basic behavior to a custom application.
    """

    # class state attribute accumulators
    _acct_vals: dict[
        str, AccountStateValue | ReservedAccountStateValue | AccountStateBlob
    ]
    _app_vals: dict[
        str,
        ApplicationStateValue | ReservedApplicationStateValue | ApplicationStateBlob,
    ]
    _precompiles: dict[str, AppPrecompile | LSigPrecompile]

    # Convenience constant fields
    address: Final[Expr] = Global.current_application_address()
    id: Final[Expr] = Global.current_application_id()

    # def __new__(cls, *args, **kwargs):
    #     self = super().__new__(cls)
    #     return self

    # def __init_subclass__(cls, **kwargs):
    #     super().__init_subclass__()
    #     cls._acct_vals = {}
    #     cls._app_vals = {}
    #     cls._precompiles = {}

    def __init__(self, version: int = MAX_TEAL_VERSION):
        """Initialize the Application, finding all the custom attributes and initializing the Router"""
        self.teal_version = version

        # get all class attribute names include names of ancestors, preserving declaration order
        cls = self.__class__
        names_ = [
            key
            for klass in reversed(cls.__mro__)
            for key in klass.__dict__
            if not key.startswith("__")
        ]
        # unique-ify values, preserving order
        names_ = list(dict.fromkeys(names_))

        # Initialize these ahead of time, may not
        # be set after init if len(precompiles)>0
        self.approval_program: Optional[str] = None
        self.clear_program: Optional[str] = None

        all_creates = []
        all_updates = []
        all_deletes = []
        all_opt_ins = []
        all_close_outs = []
        all_clear_states = []

        self.hints: dict[str, MethodHints] = {}
        self.bare_externals: dict[str, OnCompleteAction] = {}
        self.methods: dict[str, tuple[ABIReturnSubroutine, Optional[MethodConfig]]] = {}

        for name in names_:
            bound_attr = getattr(self, name)

            # Check for externals and internal methods
            handler_config = get_handler_config(bound_attr)

            # Bare externals
            if handler_config.bare_method is not None:
                actions = {
                    oc: cast(OnCompleteAction, action)
                    for oc, action in handler_config.bare_method.__dict__.items()
                    if action.action is not None
                }

                for oc, action in actions.items():
                    if oc in self.bare_externals:
                        raise BareOverwriteError(oc)

                    # Swap the implementation with the bound version
                    if handler_config.referenced_self:
                        if not (
                            isinstance(action.action, SubroutineFnWrapper)
                            or isinstance(action.action, ABIReturnSubroutine)
                        ):
                            raise TealInputError(
                                f"Expected Subroutine or ABIReturnSubroutine, for {oc} got {action.action}"
                            )
                        action.action.subroutine.implementation = bound_attr

                    self.bare_externals[oc] = action

            # ABI externals
            elif handler_config.method_spec is not None and not handler_config.internal:
                # Create the ABIReturnSubroutine from the static attr
                # but override the implementation with the bound version
                static_attr = getattr_static(self, name)
                abi_meth = ABIReturnSubroutine(
                    static_attr, overriding_name=handler_config.method_spec.name
                )

                if handler_config.referenced_self:
                    abi_meth.subroutine.implementation = bound_attr

                self.methods[name] = (abi_meth, handler_config.method_config)

                if handler_config.is_create():
                    all_creates.append(static_attr)
                if handler_config.is_update():
                    all_updates.append(static_attr)
                if handler_config.is_delete():
                    all_deletes.append(static_attr)
                if handler_config.is_opt_in():
                    all_opt_ins.append(static_attr)
                if handler_config.is_clear_state():
                    all_clear_states.append(static_attr)
                if handler_config.is_close_out():
                    all_close_outs.append(static_attr)

                self.methods[name] = (abi_meth, handler_config.method_config)
                self.hints[name] = handler_config.hints()

            # Internal subroutines
            elif handler_config.subroutine is not None:
                if handler_config.referenced_self:
                    setattr(self, name, handler_config.subroutine(bound_attr))
                else:
                    static_attr = getattr_static(self, name)
                    setattr(
                        cls,
                        name,
                        handler_config.subroutine(static_attr),
                    )

        self.on_create = all_creates.pop() if len(all_creates) == 1 else None
        self.on_update = all_updates.pop() if len(all_updates) == 1 else None
        self.on_delete = all_deletes.pop() if len(all_deletes) == 1 else None
        self.on_opt_in = all_opt_ins.pop() if len(all_opt_ins) == 1 else None
        self.on_close_out = all_close_outs.pop() if len(all_close_outs) == 1 else None
        self.on_clear_state = (
            all_clear_states.pop() if len(all_clear_states) == 1 else None
        )

        self.acct_state = AccountState(self._acct_vals)
        self.app_state = ApplicationState(self._app_vals)
        self.precompiles = self._precompiles.copy()

        # If there are no precompiles, we can build the programs
        # with what we already have and don't need to pass an
        # algod client
        if not self.precompiles:
            self.compile()

    def compile(self, client: Optional[AlgodClient] = None) -> tuple[str, str]:
        """Fully compile the application to TEAL

        Note: If the application has ``Precompile`` fields, the ``client`` must be passed to
        compile them into bytecode.

        Args:
            client (optional): An Algod client that can be passed to ``Precompile`` to have them fully compiled.
        """
        if self.approval_program is not None and self.clear_program is not None:
            return self.approval_program, self.clear_program

        # make sure all the precompiles are available
        for precompile in self.precompiles.values():
            precompile.compile(client)  # type: ignore

        router = Router(
            name=self.__class__.__name__,
            bare_calls=BareCallActions(**self.bare_externals),
            descr=self.__doc__,
        )

        # Add method externals
        for _, method_tuple in self.methods.items():
            method, method_config = method_tuple
            router.add_method_handler(
                method_call=method,
                method_config=method_config,
                overriding_name=method.name(),
            )

        # Compile approval and clear programs
        (
            self.approval_program,
            self.clear_program,
            self.contract,
        ) = router.compile_program(
            version=self.teal_version,
            assemble_constants=True,
            optimize=OptimizeOptions(scratch_slots=True),
        )

        return self.approval_program, self.clear_program

    def application_spec(self) -> dict[str, Any]:
        """returns a dictionary, helpful to provide to callers with information about the application specification"""

        if self.approval_program is None or self.clear_program is None:
            raise Exception(
                "approval or clear program are none, please build the programs first"
            )

        return {
            "hints": {k: v.dictify() for k, v in self.hints.items() if not v.empty()},
            "source": {
                "approval": base64.b64encode(self.approval_program.encode()).decode(
                    "utf8"
                ),
                "clear": base64.b64encode(self.clear_program.encode()).decode("utf8"),
            },
            "schema": {
                "local": self.acct_state.dictify(),
                "global": self.app_state.dictify(),
            },
            "contract": self.contract.dictify(),
        }

    def initialize_application_state(self) -> Expr:
        """
        Initialize any application state variables declared

        :return: The Expr to initialize the application state.
        :rtype: pyteal.Expr
        """
        return self.app_state.initialize()

    def initialize_account_state(self, addr: Expr = Txn.sender()) -> Expr:
        """
        Initialize any account state variables declared

        :param addr: Optional, address of account to initialize state for.
        :return: The Expr to initialize the account state.
        :rtype: pyteal.Expr
        """

        return self.acct_state.initialize(addr)

    @create
    def create(self) -> Expr:
        """create is the only handler defined by default and only approves the transaction.

        Override this method to define custom behavior.
        """
        return Approve()

    def dump(self, directory: str = ".", client: Optional[AlgodClient] = None) -> None:
        """write out the artifacts generated by the application to disk

        Args:
            directory (optional): str path to the directory where the artifacts should be written
            client (optional): AlgodClient to be passed to any precompiles
        """
        if self.approval_program is None:
            if self.precompiles and client is None:
                raise Exception(
                    "Approval program empty, if you have precompiles, pass an Algod client to build the precompiles"
                )
            self.compile(client)

        import json
        import os

        if not os.path.exists(directory):
            os.mkdir(directory)

        with open(os.path.join(directory, "approval.teal"), "w") as f:
            if self.approval_program is None:
                raise Exception("Approval program empty")
            f.write(self.approval_program)

        with open(os.path.join(directory, "clear.teal"), "w") as f:
            if self.clear_program is None:
                raise Exception("Clear program empty")
            f.write(self.clear_program)

        with open(os.path.join(directory, "contract.json"), "w") as f:
            if self.contract is None:
                raise Exception("Contract empty")
            f.write(json.dumps(self.contract.dictify(), indent=4))

        with open(os.path.join(directory, "application.json"), "w") as f:
            f.write(json.dumps(self.application_spec(), indent=4))
