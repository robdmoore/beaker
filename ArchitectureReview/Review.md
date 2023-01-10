# Beaker productionisation review

## Context

{what is beaker - 1 liner}. {Why is beaker useful / important}. {How does Beaker fit into AlgoKit strategy}. {Why is this review being conducted (important part of AlgoKit MVP, can probably grab something from kickoff deck)}. Link to testing strategy architecture decision.

## Goal

{Goal of review - get it ready for production use, have confidence in architecture, reduce likelihood of need for breaking changes soon after release by getting key recommended breaking changes identified now, etc.}.

## Findings summary

{tl;dr}

The findings are split into 2 categories, recommendations for immediate improvement and future suggestions.

The recommended areas for immediate improvement are:

* {One per line with a very short description of each}

The recommended areas for future improvement are:

* {One per line with a very short description of each}

## Immediate recommendations

### (1) Replace the class-based structure with an instance based one

#### What?

Beaker is currently structured around the `beaker.Application` class, which is expected to be sub-classed, and to hold the state variables (from `beaker.state.*`) and contain methods which are forwarded to the `pyteal.abi.Router` instance created during `Application.compile(...)` based on a decorator from `beaker.decorators.*`. We propose replacing this with an "instance based structure", drawing inspiration from highly popular Python web frameworks such as `flask`.

This change will simplify Beaker's code, and, more importantly, reduce the potential for end-user error.

#### Why?

User-facing benefits:
1. The current structure, by encouraging & supporting bound instance methods, is a potential source of confusion for users new to writing smart contracts / PyTeal / so on. The distinction between what runs on `beaker.Application` instantiation, evaluation by PyTeal during compile, and finally what runs on-chain, can be difficult to grasp at first. One might assume (wrongly) that Beaker is somehow maintaining the state of `self.*` between methods, but this is not the case. Contrast this with Solidity, for example.
2. Currently, actually using `self.*` can easily lead to problems, since if they are not defined before calling `super().__init__(...)` they won't be defined when compiling. This can be fixed by not automatically compiling in `Application.__init__()` (which is also proposed) for simple constants, however another issue is that using `self.foo = <Some beaker.state object>`, would not currently work with the introspection beaker is performing. This could potentially be fixed by itself, but then we're back to having to define these values _before_ calling `super().__init__()` which is a source of confusion (usually, but not always, idiomatic Python will call super init sooner rather than later).
3. In order to compose applications together - say if there were two ARC standard implementations that we wanted to combine into the same contract for some reason - the user doesn't need to understand Python's multiple-inheritance details when it comes to things like Method Resolution Order. Arguably the case here is slim, but it also makes implementation of such templates easier as well, as taking a functional composition approach means we can have easy to understand entry points where you can check any pre-conditions.
4. Since technically the way to define state variables currently actually puts them as class variables, this makes them "globals" in a sense. Which could lead to errors/bugs in a class hierarchy situation, when trying to modify the values here - say for instance:
   ```python
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
   assert app1.approval_program != app2.approval_program  # fails

   ```
5. Setting parameters that control the program creation is somewhat awkward - currently this is just the `verion` parameter which specifies the TEAL version, but we can imagine there being more opportunities for simple configuration here in an instance based-approach.

The main benefit to Beaker is the removal the mangling of function signatures to remove `self` will reduce the complexity of the code. Currently, it only works by convention. And the compatibility with PyTeal relies on implementation details therein.

There are also bugs in beaker which are directly caused by the class-based structure. For example, bare methods are currently evaluated as a subroutine only once:

   ```python
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
 
   ```

#### Before & After - user's perspective
The proposed changes are fairly substantial internally, and propose a radically different architecture conceptually for beaker Applications, but the migration should actually be relatively straight forward for existing code:


The following examples assume the import of relevant names from `beaker` and/or `pyteal` in order to focus on core differences.

Before:
```python

class CounterApp(Application):
    counter = ApplicationStateValue(
        stack_type=TealType.uint64,
        descr="A counter for showing how to use application state",
    )

    @create
    def create(self):
        return self.initialize_application_state()

    @external(authorize=Authorize.only(Global.creator_address()))
    def increment(self, *, output: abi.Uint64):
        """increment the counter"""
        return Seq(
            self.counter.set(self.counter + Int(1)),
            output.set(self.counter),
        )

    @external(authorize=Authorize.only(Global.creator_address()))
    def decrement(self, *, output: abi.Uint64):
        """decrement the counter"""
        return Seq(
            self.counter.set(self.counter - Int(1)),
            output.set(self.counter),
        )

```

After:
```python

class CounterState(beaker.State):
    counter = ApplicationStateValue(
        stack_type=TealType.uint64,
        descr="A counter for showing how to use application state",
    )
    
    
app = beaker.Application(state=CounterState())

@app.create
def create():
    return app.state.initialize_application_state()

@app.external(authorize=Authorize.only(Global.creator_address()))
def increment(*, output: abi.Uint64):
    """increment the counter"""
    return Seq(
        app.state.counter.set(app.state.counter + Int(1)),
        output.set(app.state.counter),
    )

@app.external(authorize=Authorize.only(Global.creator_address()))
def decrement(*, output: abi.Uint64):
    """decrement the counter"""
    return Seq(
        app.state.counter.set(app.state.counter - Int(1)),
        output.set(app.state.counter),
    )

```

### (2) Defer compilation

#### What?

Currently, `beaker.Application.compile()` is called as part of `__init__()`, assuming there are no `precompiles` defined. We recommend that `compile()` always be deferred to a later point, and further that `compile()` does not mutate `Application` in any way, but instead returns a new object.

#### Why?

The deferment of the `compile()` call is actually a necessary part of recommendation #1 that we have skipped over thus far, but would be recommended anyway.

The immediate `compile()` has issues such as requiring implementors (ie subclasses) to call `super().__init__()` as a final step in their own `__init__` method - any code that runs after the super init call will have no effect on the application produced.

Immediate compilation also reduces the control the user has over the output. Although currently the only parameter that `compile` takes is a `client`, it might be useful to add (optional) parameters here to control the compilation, like with the optimisations applied as an example. If new optimisations are introduced and enabled by default, this could alter the output of any existing contracts, which might not be desired.

The separation of compiled state outside of `Application` simplifies the design, and can be done mostly transparently to end-users.

The separation of compiled state will also benefit future interoperability. Once `beaker.client` is split into a separate package, if the compiled state can be both generated from a beaker Application object _or_ loaded from disk (or similar), this means Beaker's ApplicationClient could be used in more situations.

#### Before & After - user's perspective

For most use cases, this should be a relatively small change.

We believe there are two common usage scenarios, currently:

1. Output the `Application` via `Application.dump(...)`
2. Interact with the `Application` by passing it to `ApplicationClient(app=..., ...)`.

We propose maintaining those two scenarios without any immediate external changes, but internally:

1. `Application.dump(...)` will call `Application.compile().dump()`, and potentially trigger a `DeprecationWarning`.
2. `ApplicationClient(app=..., ...)` will call `Application.compile()` and not retain any reference to `app`.

To make use of scenarios 1 & 2, or to control compilation parameters, a user should also be able to:

```python
app = Application(...)
compiled_app: CompiledApplication = app.compile(...)
compiled_app.dump(...)
client = ApplicationClient(app=compiled_app, ...)
```
We suggest also potentially renaming `CompiledApplication.dump()`, perhaps to something along the lines of `serialize()`.

The exact details of what `CompiledApplication` will look like are TBD, but should be driven by the principles outlined in the "Why?" section above.
