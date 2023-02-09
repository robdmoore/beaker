import pyteal
import pytest
import pyteal as pt
from typing import Any
from base64 import b64decode, b64encode

from algosdk.account import generate_account
from algosdk.logic import get_application_address
from algosdk.transaction import Multisig, LogicSigAccount, OnComplete
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    MultisigTransactionSigner,
    LogicSigTransactionSigner,
)

from beaker.decorators import Authorize
from beaker.sandbox import get_accounts, get_algod_client
from beaker.application import Application, CompilerOptions
from beaker.application_specification import DefaultArgument
from beaker.state import ApplicationStateValue, AccountStateValue
from beaker.client.application_client import ApplicationClient
from beaker.client.logic_error import LogicException


class AppState:
    app_state_val_int = ApplicationStateValue(pt.TealType.uint64, default=pt.Int(1))
    app_state_val_byte = ApplicationStateValue(
        pt.TealType.bytes, default=pt.Bytes("test")
    )
    acct_state_val_int = AccountStateValue(pt.TealType.uint64, default=pt.Int(1))
    acct_state_val_byte = AccountStateValue(pt.TealType.bytes, default=pt.Bytes("test"))


def App(version: int = pyteal.MAX_PROGRAM_VERSION) -> Application:
    app = Application(
        "App",
        state=AppState,
        compiler_options=CompilerOptions(avm_version=version),
    )

    @app.create(bare=True)
    def create():
        return pt.Seq(
            app.initialize_application_state(),
            pt.Assert(pt.Len(pt.Txn.note()) == pt.Int(0)),
            pt.Approve(),
        )

    @app.update(authorize=Authorize.only(pt.Global.creator_address()), bare=True)
    def update():
        return pt.Approve()

    @app.delete(authorize=Authorize.only(pt.Global.creator_address()), bare=True)
    def delete():
        return pt.Approve()

    @app.opt_in(bare=True)
    def opt_in():
        return pt.Seq(
            app.initialize_account_state(),
            pt.Assert(pt.Len(pt.Txn.note()) == pt.Int(0)),
            pt.Approve(),
        )

    @app.clear_state
    def clear_state():
        return pt.Seq(pt.Assert(pt.Len(pt.Txn.note()) == pt.Int(0)), pt.Approve())

    @app.close_out(bare=True)
    def close_out():
        return pt.Seq(pt.Assert(pt.Len(pt.Txn.note()) == pt.Int(0)), pt.Approve())

    @app.external
    def add(a: pt.abi.Uint64, b: pt.abi.Uint64, *, output: pt.abi.Uint64):
        return output.set(a.get() + b.get())

    @app.external(read_only=True)
    def dummy(*, output: pt.abi.String):
        return output.set("deadbeef")

    return app


SandboxAccounts = list[tuple[str, str, AccountTransactionSigner]]


@pytest.fixture(scope="session")
def sb_accts() -> SandboxAccounts:
    return [(acct.address, acct.private_key, acct.signer) for acct in get_accounts()]


def test_app_client_create():
    app = App()
    client = get_algod_client()
    ac = ApplicationClient(client, app)
    assert ac.signer is None, "Should not have a signer"
    assert ac.sender is None, "Should not have a sender"
    assert ac.app_id == 0, "Should not have app id"
    assert ac.app_addr is None, "Should not have app address"
    assert ac.suggested_params is None, "Should not have suggested params"

    with pytest.raises(Exception):
        ac.get_signer(None)

    with pytest.raises(Exception):
        ac.get_sender(None, None)


def test_app_prepare(sb_accts: SandboxAccounts):
    app = App()
    client = get_algod_client()

    (addr, sk, signer) = sb_accts[0]

    ac_with_signer = ApplicationClient(client, app, signer=signer)

    assert ac_with_signer.signer == signer, "Should have the same signer"

    assert ac_with_signer.get_signer(None) == signer, "Should produce the same signer"
    assert (
        ac_with_signer.get_sender(None, None) == addr
    ), "Should produce the same address"

    new_pk, new_addr = generate_account()
    new_signer = AccountTransactionSigner(new_pk)
    ac_with_signer_and_sender = ac_with_signer.prepare(sender=new_addr)

    assert (
        ac_with_signer_and_sender.signer == signer
    ), "Should have the same original signer"
    assert (
        ac_with_signer_and_sender.sender == new_addr
    ), "Should have the new addr as sender"

    assert (
        ac_with_signer_and_sender.get_signer(None) == signer
    ), "Should produce the same signer"

    assert (
        ac_with_signer_and_sender.get_sender(None, None) == new_addr
    ), "Should produce the new address"

    assert (
        ac_with_signer_and_sender.get_signer(new_signer) == new_signer
    ), "Should be new signer"
    assert (
        ac_with_signer_and_sender.get_sender(None, new_signer) == new_addr
    ), "Should be new address"

    accts = [generate_account() for _ in range(3)]
    addrs = [acct[1] for acct in accts]
    sks = [acct[0] for acct in accts]

    msig_acct = Multisig(1, 3, addrs)
    msts = MultisigTransactionSigner(msig_acct, sks[0])

    ac_with_msig = ac_with_signer.prepare(signer=msts)
    assert ac_with_msig.signer == msts, "Should have the same signer"
    assert (
        ac_with_msig.sender == msig_acct.address()
    ), "Should have the address of the msig as the sender"
    assert ac_with_msig.get_signer(None) == msts, "Should produce the same signer"
    assert (
        ac_with_msig.get_sender(None, None) == msig_acct.address()
    ), "Should produce the same address"

    # pragma version 6; int 1; return
    program = b64decode("BoEBQw==")
    lsig = LogicSigAccount(program)
    lsig_signer = LogicSigTransactionSigner(lsig)

    ac_with_lsig = ac_with_signer.prepare(signer=lsig_signer)
    assert ac_with_lsig.signer == lsig_signer, "Should have the same signer"
    assert (
        ac_with_lsig.sender == lsig.address()
    ), "Should have the address of the lsig as the sender"
    assert (
        ac_with_lsig.get_signer(None) == lsig_signer
    ), "Should produce the same signer"
    assert (
        ac_with_lsig.get_sender(None, None) == lsig.address()
    ), "Should produce the same address"

    ac_with_app_id = ac_with_signer.prepare(app_id=3)
    assert (
        ac_with_signer.app_id == 0
    ), "We should not have changed the app id in the original"
    assert (
        ac_with_app_id.app_id == 3
    ), "We should have overwritten the app id in the new version"


def expect_dict(actual: dict[str, Any], expected: dict[str, Any]):
    for k, v in expected.items():
        if type(v) is dict:
            expect_dict(actual[k], v)
        else:
            assert actual[k] == v, f"for field {k}, expected {v} got {actual[k]}"


def test_create(sb_accts: SandboxAccounts):
    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, app_addr, tx_id = ac.create()
    assert app_id > 0
    assert app_addr == get_application_address(app_id)
    assert ac.app_id == app_id
    assert ac.app_addr == app_addr

    result_tx = client.pending_transaction_info(tx_id)
    assert result_tx["confirmed-round"] > 0
    expect_dict(
        result_tx,
        {
            "application-index": app_id,
            "pool-error": "",
            "txn": {
                "txn": {
                    "snd": addr,
                    "apgs": {"nbs": 1, "nui": 1},
                    "apls": {"nbs": 1, "nui": 1},
                }
            },
        },
    )

    new_addr, new_pk, new_signer = sb_accts[1]
    new_ac = ac.prepare(signer=new_signer)
    extra_pages = 2
    sp = client.suggested_params()
    sp.fee = 1_000_000
    sp.flat_fee = True
    app_id, app_addr, tx_id = new_ac.create(
        extra_pages=extra_pages, suggested_params=sp
    )
    assert app_id > 0
    assert app_addr == get_application_address(app_id)
    assert new_ac.app_id == app_id
    assert new_ac.app_addr == app_addr

    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "application-index": app_id,
            "pool-error": "",
            "txn": {
                "txn": {
                    "snd": new_addr,
                    "apep": extra_pages,
                    "fee": sp.fee,
                    "apgs": {"nbs": 1, "nui": 1},
                    "apls": {"nbs": 1, "nui": 1},
                }
            },
        },
    )

    with pytest.raises(LogicException):
        ac.create(note="failmeplz")


def test_update(sb_accts: SandboxAccounts):
    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, app_addr, _ = ac.create()

    tx_id = ac.update()
    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apan": OnComplete.UpdateApplicationOC,
                    "apid": app_id,
                    "snd": addr,
                }
            },
        },
    )

    with pytest.raises(LogicException):
        addr, pk, signer2 = sb_accts[1]
        ac2 = ac.prepare(signer=signer2)
        ac2.update()


def test_delete(sb_accts: SandboxAccounts):
    app = App()
    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    tx_id = ac.delete()
    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apan": OnComplete.DeleteApplicationOC,
                    "apid": app_id,
                    "snd": addr,
                }
            },
        },
    )

    with pytest.raises(LogicException):
        ac = ApplicationClient(client, app, signer=signer)
        app_id, _, _ = ac.create()

        _, _, signer2 = sb_accts[1]
        ac2 = ac.prepare(signer=signer2)
        ac2.delete()


def test_opt_in(sb_accts: SandboxAccounts):
    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    new_addr, new_pk, new_signer = sb_accts[1]
    new_ac = ac.prepare(signer=new_signer)
    tx_id = new_ac.opt_in()
    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apan": OnComplete.OptInOC,
                    "apid": app_id,
                    "snd": new_addr,
                }
            },
        },
    )

    with pytest.raises(LogicException):
        _, _, newer_signer = sb_accts[2]
        newer_ac = ac.prepare(signer=newer_signer)
        newer_ac.opt_in(note="failmeplz")


def test_close_out(sb_accts: SandboxAccounts):

    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    new_addr, new_pk, new_signer = sb_accts[1]
    new_ac = ac.prepare(signer=new_signer)
    new_ac.opt_in()

    tx_id = new_ac.close_out()
    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apan": OnComplete.CloseOutOC,
                    "apid": app_id,
                    "snd": new_addr,
                }
            },
        },
    )

    with pytest.raises(LogicException):
        _, _, newer_signer = sb_accts[2]
        newer_ac = ac.prepare(signer=newer_signer)
        newer_ac.opt_in()
        newer_ac.close_out(note="failmeplz")


def test_clear_state(sb_accts: SandboxAccounts):
    app = App()
    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    new_addr, new_pk, new_signer = sb_accts[1]
    new_ac = ac.prepare(signer=new_signer)
    new_ac.opt_in()

    tx_id = new_ac.clear_state()
    result_tx = client.pending_transaction_info(tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apan": OnComplete.ClearStateOC,
                    "apid": app_id,
                    "snd": new_addr,
                }
            },
        },
    )


def test_call(sb_accts: SandboxAccounts):
    app = App()
    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    method_add = app.abi_methods["add"]
    result = ac.call(method_add, a=1, b=1)
    assert result.return_value == 2
    assert result.decode_error is None
    assert result.raw_value == (2).to_bytes(8, "big")

    ms = method_add.method_spec().get_selector()
    raw_args = [ms, (1).to_bytes(8, "big"), (1).to_bytes(8, "big")]

    return_prefix = 0x151F7C75
    log_msg = b64encode(
        return_prefix.to_bytes(4, "big") + (2).to_bytes(8, "big")
    ).decode("utf-8")

    result_tx = client.pending_transaction_info(result.tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apaa": [b64encode(arg).decode("utf-8") for arg in raw_args],
                    "apid": app_id,
                    "snd": addr,
                }
            },
            "logs": [log_msg],
        },
    )


def test_add_method_call(sb_accts: SandboxAccounts):
    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)
    app_id, _, _ = ac.create()

    method_add = app.abi_methods["add"]
    atc = AtomicTransactionComposer()
    ac.add_method_call(atc, method_add, a=1, b=1)
    atc_result = atc.execute(client, 4)
    result = atc_result.abi_results[0]

    assert result.return_value == 2
    assert result.decode_error is None
    assert result.raw_value == (2).to_bytes(8, "big")

    ms = method_add.method_spec().get_selector()
    raw_args = [ms, (1).to_bytes(8, "big"), (1).to_bytes(8, "big")]

    return_prefix = 0x151F7C75
    log_msg = b64encode(
        return_prefix.to_bytes(4, "big") + (2).to_bytes(8, "big")
    ).decode("utf-8")

    result_tx = client.pending_transaction_info(result.tx_id)
    expect_dict(
        result_tx,
        {
            "pool-error": "",
            "txn": {
                "txn": {
                    "apaa": [b64encode(arg).decode("utf-8") for arg in raw_args],
                    "apid": app_id,
                    "snd": addr,
                }
            },
            "logs": [log_msg],
        },
    )


def test_fund(sb_accts: SandboxAccounts):
    app = App()
    addr, pk, signer = sb_accts[0]
    client = get_algod_client()

    fund_amt = 1_000_000

    ac = ApplicationClient(client, app, signer=signer)
    ac.create()
    ac.fund(fund_amt)

    info = ac.get_application_account_info()
    assert info["amount"] == fund_amt, "Expected balance to equal fund_amt"


def test_resolve(sb_accts: SandboxAccounts):

    app = App()

    addr, pk, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, app, signer=signer)

    ac.create()
    ac.opt_in()

    assert ac.resolve(DefaultArgument.from_resolver(pt.Int(1))) == 1
    assert ac.resolve(DefaultArgument.from_resolver(pt.Bytes("stringy"))) == "stringy"
    assert ac.resolve(DefaultArgument.from_resolver(AppState.app_state_val_int)) == 1
    assert (
        ac.resolve(DefaultArgument.from_resolver(AppState.app_state_val_byte))
        == b"test"
    )
    assert ac.resolve(DefaultArgument.from_resolver(AppState.acct_state_val_int)) == 1
    assert (
        ac.resolve(DefaultArgument.from_resolver(AppState.acct_state_val_byte))
        == b"test"
    )
    assert (
        ac.resolve(DefaultArgument.from_resolver(app.abi_methods["dummy"]))
        == "deadbeef"
    )


def test_override_app_create(sb_accts: SandboxAccounts):
    sc = Application("SpecialCreate")

    @sc.create
    def create(x: pt.abi.Uint64, *, output: pt.abi.Uint64):
        return output.set(x.get())

    sc.build()

    _, _, signer = sb_accts[0]

    client = get_algod_client()
    ac = ApplicationClient(client, sc, signer=signer)
    assert ac.on_create

    val = 2

    app_id, _, txid = ac.create(x=val)
    assert app_id > 0

    txinfo = client.pending_transaction_info(txid)
    assert txinfo["application-index"] == app_id

    retlog = b64decode(txinfo["logs"][0])
    assert retlog[4:] == val.to_bytes(8, "big")
