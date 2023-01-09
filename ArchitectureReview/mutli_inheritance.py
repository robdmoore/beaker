import pyteal
import beaker


class MyBaseApp(beaker.Application):
    counter = beaker.ApplicationStateValue(stack_type=pyteal.TealType.uint64)

    @beaker.create
    def create(self) -> pyteal.Expr:
        return self.initialize_application_state()


class MyApp(MyBaseApp):
    pass


MyApp.counter.default = pyteal.Int(10)


class MyOtherApp(MyBaseApp):
    pass


app1 = MyApp()
app2 = MyOtherApp()

assert app1.approval_program != app2.approval_program
