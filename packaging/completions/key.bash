_key_completion() {
    local current="${COMP_WORDS[COMP_CWORD]}"
    local command="${COMP_WORDS[1]:-}"
    local subcommand="${COMP_WORDS[2]:-}"
    local words

    if (( COMP_CWORD == 1 )); then
        words="version shell ipc doctor audio record cast clipboard top weather theme install update uninstall component rollback release setup migrate"
    elif [[ "$command" == theme && "$subcommand" == zsh ]]; then
        words="list status show hide toggle reset"
    elif [[ "$command" == theme ]]; then
        words="zsh"
    elif [[ "$command" == install ]]; then
        words="keytop clavis-zsh-theme clavis-fcitx5-theme"
    elif [[ "$command" == component ]]; then
        words="status"
    elif [[ "$command" == release ]]; then
        words="list remove install-finalize"
    elif [[ "$command" == clipboard ]]; then
        words="list inspect preview restore delete clear status watch"
    elif [[ "$command" == audio ]]; then
        words="start status stop"
    elif [[ "$command" == record ]]; then
        words="start status stop"
    elif [[ "$command" == cast ]]; then
        words="list status"
    else
        words="--help --version --json --source --dry-run"
    fi

    COMPREPLY=( $(compgen -W "$words" -- "$current") )
}

complete -F _key_completion key
