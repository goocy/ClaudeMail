// ClaudeMail LaunchAgent launcher — the app bundle's main executable.
//
// Copied from ../ExportScreenTime/launcher.c (same design as the owner's other launchers).
// The source sits BESIDE the bundle, not inside it: a loose file in Contents/ is unsealed and fails
// `codesign --verify --strict`.
// A compiled, code-signed Mach-O at Contents/MacOS/<CFBundleExecutable> runs *as* the bundle, so
// the watcher shows up as ClaudeMail.app (Login Items, Activity Monitor, any privacy prompt) rather
// than as a bare Homebrew python whose identity changes with every `brew upgrade`. It launches
// python as a *child* and waits, so this process stays resident as the bundle identity and the
// child inherits any TCC grant through responsible-process attribution.
// ClaudeMail itself needs no grant today: the mailbox is in ~/ResilioSync, which TCC does not guard.
//
// argv[1..] are forwarded verbatim to python — the LaunchAgent plist stays the source of truth for
// the script path and options (mail.py watch). Nothing service-specific is compiled in beyond the
// name in the error messages, so this file should never need to change.

#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

extern char **environ;

int main(int argc, char *argv[]) {
    // Homebrew interpreters, most-preferred first. python3.12 is pinned+mature; python3 is the
    // rolling default fallback. /opt/homebrew is Apple Silicon (Altiplano), /usr/local is Intel (Sandbank).
    const char *candidates[] = {
        "/opt/homebrew/bin/python3.12",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3.12",
        "/usr/local/bin/python3",
        NULL,
    };

    for (int i = 0; candidates[i] != NULL; i++) {
        if (access(candidates[i], X_OK) != 0)
            continue;

        // Child argv: [python, <forwarded argv[1..]>, NULL].
        char **child_argv = calloc((size_t)argc + 1, sizeof(char *));
        if (child_argv == NULL) {
            fprintf(stderr, "ClaudeMail launcher: out of memory\n");
            return 1;
        }
        child_argv[0] = (char *)candidates[i];
        for (int j = 1; j < argc; j++)
            child_argv[j] = argv[j];
        child_argv[argc] = NULL;

        pid_t pid;
        // NULL file actions => the child inherits our stdio (launchd wired it to ~/.claude/claudemail/logs/launchd.log).
        int rc = posix_spawn(&pid, candidates[i], NULL, NULL, child_argv, environ);
        if (rc != 0) {
            fprintf(stderr, "ClaudeMail launcher: posix_spawn(%s) failed: %s\n",
                    candidates[i], strerror(rc));
            free(child_argv);
            continue;
        }

        int status = 0;
        while (waitpid(pid, &status, 0) < 0) {
            // retry on EINTR
        }
        free(child_argv);

        if (WIFEXITED(status))
            return WEXITSTATUS(status);
        return 1;
    }

    fprintf(stderr, "ClaudeMail launcher: no Homebrew python found "
                    "(looked for python3.12 and python3 in /opt/homebrew/bin and /usr/local/bin). "
                    "Install one with: brew install python@3.12\n");
    return 1;
}
