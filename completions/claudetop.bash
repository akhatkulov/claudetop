# claudetop uchun bash completion
# O'rnatish:  source completions/claudetop.bash
#   yoki:     cp completions/claudetop.bash /etc/bash_completion.d/claudetop
_claudetop() {
  local cur prev opts views
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  opts="-w --watch --once -c --compact --report --csv -n --interval \
        --view --session --theme --notify --set-limit --json --no-color -h --help -v --version"
  views="overview sessions activity trends insights"
  case "$prev" in
    --view) COMPREPLY=($(compgen -W "$views" -- "$cur")); return ;;
    --theme) COMPREPLY=($(compgen -W "default mono ocean matrix amber" -- "$cur")); return ;;
    --set-limit) COMPREPLY=($(compgen -W "session= weekly=" -- "$cur")); return ;;
    -n|--interval) return ;;
  esac
  COMPREPLY=($(compgen -W "$opts" -- "$cur"))
}
complete -F _claudetop claudetop
