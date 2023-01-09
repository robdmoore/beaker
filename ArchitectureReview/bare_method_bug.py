import pyteal
import beaker


class MyApp(beaker.Application):
    price = beaker.ApplicationStateValue(stack_type=pyteal.TealType.uint64)

    def __init__(self, default_price: int, version: int = pyteal.MAX_TEAL_VERSION):
        self.price.default = pyteal.Int(default_price)
        super().__init__(version=version)


class CorrectApp(MyApp):
    @beaker.create
    def create(self, *, output: pyteal.abi.Uint64) -> pyteal.Expr:
        return pyteal.Seq(self.initialize_application_state(), output.set(self.price))


class IncorrectApp(MyApp):
    @beaker.create
    def create(self) -> pyteal.Expr:
        return self.initialize_application_state()


correct_app1 = CorrectApp(default_price=123)
correct_app2 = CorrectApp(default_price=456)

incorrect_app1 = IncorrectApp(default_price=123)
incorrect_app2 = IncorrectApp(default_price=456)

assert correct_app1.approval_program != correct_app2.approval_program  # success
assert incorrect_app1.approval_program != incorrect_app2.approval_program  # failure
