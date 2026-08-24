"""Shell completion installation and script generation for ProfileDock.

Replaces Typer's generic PowerShell installer with ProfileDock-specific
behavior: the completer registers in the all-hosts profile so every host
(classic console, VS Code integrated terminal, ISE) picks it up, the user's
execution policy is never modified, and the user's Tab key handler is never
rebound. Installation is idempotent.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import shellingham
except ImportError:  # pragma: no cover - shellingham ships as a typer dependency
    shellingham = None

SUPPORTED_SHELLS = ("bash", "zsh", "fish", "powershell", "pwsh")
_COMPLETION_MARKER = "PROFILEDOCK-COMPLETION"


class CompletionError(Exception):
    pass


_COMPLETION_SCRIPT_BASH = """
%(complete_func)s() {
    local IFS=$'\\n'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   %(autocomplete_var)s=complete_bash $1 ) )
    return 0
}

complete -o default -F %(complete_func)s %(prog_name)s
"""

_COMPLETION_SCRIPT_ZSH = """
#compdef %(prog_name)s

%(complete_func)s() {
  eval $(env _TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}" %(autocomplete_var)s=complete_zsh %(prog_name)s)
}

compdef %(complete_func)s %(prog_name)s
"""

_COMPLETION_SCRIPT_FISH = (
    "complete --command %(prog_name)s --no-files --arguments "
    '"(env %(autocomplete_var)s=complete_fish _TYPER_COMPLETE_FISH_ACTION=get-args '
    '_TYPER_COMPLETE_ARGS=(commandline -cp) %(prog_name)s)" '
    '--condition "(env %(autocomplete_var)s=complete_fish _TYPER_COMPLETE_FISH_ACTION=is-args '
    '_TYPER_COMPLETE_ARGS=(commandline -cp) %(prog_name)s)"'
)

# Deliberately omits Import-Module PSReadLine / Set-PSReadLineKeyHandler: those
# would globally rebind the user's Tab handler for every program.
_COMPLETION_SCRIPT_POWER_SHELL = """
$scriptblock = {
    param($wordToComplete, $commandAst, $cursorPosition)
    $Env:%(autocomplete_var)s = "complete_powershell"
    $Env:_TYPER_COMPLETE_ARGS = $commandAst.ToString()
    $Env:_TYPER_COMPLETE_WORD_TO_COMPLETE = $wordToComplete
    %(prog_name)s | ForEach-Object {
        $commandArray = $_ -Split ":::"
        $command = $commandArray[0]
        $helpString = $commandArray[1]
        [System.Management.Automation.CompletionResult]::new(
            $command, $command, 'ParameterValue', $helpString)
    }
    $Env:%(autocomplete_var)s = ""
    $Env:_TYPER_COMPLETE_ARGS = ""
    $Env:_TYPER_COMPLETE_WORD_TO_COMPLETE = ""
}
Register-ArgumentCompleter -Native -CommandName %(prog_name)s -ScriptBlock $scriptblock
"""

_TEMPLATES = {
    "bash": _COMPLETION_SCRIPT_BASH,
    "zsh": _COMPLETION_SCRIPT_ZSH,
    "fish": _COMPLETION_SCRIPT_FISH,
    "powershell": _COMPLETION_SCRIPT_POWER_SHELL,
    "pwsh": _COMPLETION_SCRIPT_POWER_SHELL,
}


def get_completion_script(*, prog_name: str, complete_var: str, shell: str) -> str:
    template = _TEMPLATES.get(shell)
    if template is None:
        raise CompletionError(f"unsupported shell '{shell}' (choose from {', '.join(SUPPORTED_SHELLS)})")
    cf_name = "".join(
        character for character in prog_name.replace("-", "_") if character.isalnum() or character == "_"
    )
    return (
        template
        % {
            "complete_func": f"_{cf_name}_completion",
            "prog_name": prog_name,
            "autocomplete_var": complete_var,
        }
    ).strip()


def complete_var_for(prog_name: str) -> str:
    return "_{}_COMPLETE".format(prog_name.replace("-", "_").upper())


def _detect_shell() -> Optional[str]:
    if shellingham is None:
        return None
    detected: Optional[str]
    try:
        name, _cmd = shellingham.detect_shell()
        detected = name
    except shellingham.ShellDetectionFailure:
        return None
    return detected


def _resolve_shell(shell: Optional[str]) -> str:
    selected = (shell or "").strip().lower()
    if not selected:
        selected = _detect_shell() or ""
        if not selected and sys.platform == "win32":
            selected = "powershell"
    if selected not in _TEMPLATES:
        raise CompletionError(f"unsupported shell '{shell}' (choose from {', '.join(SUPPORTED_SHELLS)})")
    return selected


def _decode_stdout(payload: bytes) -> str:
    for encoding in ("windows-1252", "utf-8", "cp850"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def install_powershell(*, prog_name: str, complete_var: str, shell: str) -> Path:
    probe = subprocess.run(
        [shell, "-NoProfile", "-NoLogo", "-Command", "$PROFILE.CurrentUserAllHosts"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    path_text = _decode_stdout(probe.stdout).strip() if probe.returncode == 0 else ""
    if not path_text:
        raise CompletionError(
            f"could not determine the {shell} all-hosts profile path; "
            "install manually with --show-completion output instead"
        )
    profile_path = Path(path_text)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if profile_path.is_file():
        existing = profile_path.read_text(encoding="utf-8", errors="replace")
    if _COMPLETION_MARKER in existing:
        return profile_path
    script = get_completion_script(prog_name=prog_name, complete_var=complete_var, shell=shell)
    with profile_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n# --- {_COMPLETION_MARKER} ---\n{script}\n")
    return profile_path


def install_bash(*, prog_name: str, complete_var: str) -> Path:
    completion_path = Path.home() / ".bash_completions" / f"{prog_name}.sh"
    rc_path = Path.home() / ".bashrc"
    rc_path.parent.mkdir(parents=True, exist_ok=True)
    rc_content = rc_path.read_text(encoding="utf-8") if rc_path.is_file() else ""
    source_line = f"source '{completion_path}'"
    if source_line not in rc_content:
        rc_content += f"\n{source_line}\n"
    rc_path.write_text(rc_content, encoding="utf-8")
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    completion_path.write_text(
        get_completion_script(prog_name=prog_name, complete_var=complete_var, shell="bash"),
        encoding="utf-8",
    )
    return completion_path


def install_zsh(*, prog_name: str, complete_var: str) -> Path:
    zshrc_path = Path.home() / ".zshrc"
    zshrc_path.parent.mkdir(parents=True, exist_ok=True)
    zshrc_content = zshrc_path.read_text(encoding="utf-8") if zshrc_path.is_file() else ""
    autoload_line = "fpath+=~/.zfunc; autoload -Uz compinit; compinit"
    if autoload_line not in zshrc_content:
        zshrc_content += f"\n{autoload_line}\n"
    if "zstyle" not in zshrc_content:
        zshrc_content += "zstyle ':completion:*' menu select\n"
    zshrc_path.write_text(f"{zshrc_content.strip()}\n", encoding="utf-8")
    func_path = Path.home() / ".zfunc" / f"_{prog_name}"
    func_path.parent.mkdir(parents=True, exist_ok=True)
    func_path.write_text(
        get_completion_script(prog_name=prog_name, complete_var=complete_var, shell="zsh"),
        encoding="utf-8",
    )
    return func_path


def install_fish(*, prog_name: str, complete_var: str) -> Path:
    completion_path = Path.home() / ".config" / "fish" / "completions" / f"{prog_name}.fish"
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    script = get_completion_script(prog_name=prog_name, complete_var=complete_var, shell="fish")
    completion_path.write_text(f"{script}\n", encoding="utf-8")
    return completion_path


def install(shell: Optional[str] = None, *, prog_name: str = "profiledock") -> tuple[str, Path]:
    selected = _resolve_shell(shell)
    complete_var = complete_var_for(prog_name)
    if selected == "bash":
        installed = install_bash(prog_name=prog_name, complete_var=complete_var)
    elif selected == "zsh":
        installed = install_zsh(prog_name=prog_name, complete_var=complete_var)
    elif selected == "fish":
        installed = install_fish(prog_name=prog_name, complete_var=complete_var)
    else:
        installed = install_powershell(prog_name=prog_name, complete_var=complete_var, shell=selected)
    return selected, installed


def show(shell: Optional[str] = None, *, prog_name: str = "profiledock") -> str:
    selected = _resolve_shell(shell)
    return get_completion_script(
        prog_name=prog_name, complete_var=complete_var_for(prog_name), shell=selected
    )
