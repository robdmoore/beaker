import inspect
import subprocess
from pathlib import Path

from beaker import Application, sandbox


def check_application_artifacts_output_stability(
    app: Application,
    dir_name: str | None = None,
    output_dir: Path | None = None,
) -> None:
    """Test that the contract output hasn't changed for an Application, using git diff

    This gives us confidence in output stability when making internal changes to Beaker.

    The output will be placed next to the class definition location, by default in a
    directory named <class name>.artifacts/

    Args:
        app (beaker.Application): an instantiated application to compile
        dir_name (str | None): optional name for directory, if you just want to customise the name
        output_dir (pathlib.Path | None): optional full path to place output, for full control

    """
    if dir_name and output_dir:
        raise ValueError("Only one of dir_name and output_dir should be specified")

    if app.precompiles:
        algod_client = sandbox.get_algod_client()
        app.compile(algod_client)

    if output_dir is None:
        app_class = app.__class__
        module_path = Path(inspect.getfile(app_class))
        module_dir = module_path.parent
        output_dir = module_dir / (dir_name or f"{app_class.__qualname__}.artifacts")

    output_dir_did_exist = output_dir.is_dir()

    output_dir_str = str(output_dir.resolve())
    app.dump(output_dir_str)
    assert output_dir.is_dir()
    git_diff = subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "--no-ext-diff",
            "--no-color",
            output_dir_str,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # first fail if there are any changes to already committed files, you must manually add them in that case
    assert git_diff.returncode == 0, git_diff.stdout

    # if first time running, we can just add (stage) the changes
    if not output_dir_did_exist:
        subprocess.run(["git", "add", output_dir_str], text=True, check=True)
