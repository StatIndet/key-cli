#include "theme_command.h"

#include <QProcess>
#include <QStandardPaths>

#include <cstdio>

namespace {

void forward(const QByteArray &data, FILE *stream)
{
    if (!data.isEmpty())
        std::fwrite(data.constData(), 1, static_cast<std::size_t>(data.size()), stream);
    std::fflush(stream);
}

} // namespace

CommandResult ThemeCommand::run(const QStringList &arguments) const
{
    if (arguments.isEmpty() || arguments.first() != QStringLiteral("zsh")) {
        return {
            2,
            false,
            {},
            QStringLiteral(
                "Usage: key theme zsh list|status [--json]\n"
                "       key theme zsh show|hide|toggle <module...>\n"
                "       key theme zsh reset"),
            true,
        };
    }

    const QString command = qEnvironmentVariable("CLAVIS_ZSH_THEME_COMMAND",
                                                 QStringLiteral("zsh-theme"));
    const QString executable = command.contains(QLatin1Char('/'))
        ? command
        : QStandardPaths::findExecutable(command);
    if (executable.isEmpty()) {
        return {
            1,
            false,
            {},
            QStringLiteral(
                "key: zsh-theme was not found. Install the clavis-zsh-theme component "
                "before using 'key theme zsh'."),
            true,
        };
    }

    QProcess process;
    process.setProgram(executable);
    process.setArguments(arguments.mid(1));
    process.setProcessChannelMode(QProcess::SeparateChannels);
    process.start();
    if (!process.waitForStarted(5000)) {
        return {
            1,
            false,
            {},
            QStringLiteral("key: unable to start zsh-theme: %1")
                .arg(process.errorString()),
            true,
        };
    }
    process.waitForFinished(-1);
    forward(process.readAllStandardOutput(), stdout);
    forward(process.readAllStandardError(), stderr);
    if (process.error() != QProcess::UnknownError
        && process.exitStatus() != QProcess::NormalExit) {
        return {
            1,
            false,
            {},
            QStringLiteral("key: zsh-theme terminated unexpectedly: %1")
                .arg(process.errorString()),
            true,
        };
    }
    return {
        process.exitCode(),
        false,
        {},
        {},
        false,
        true,
    };
}
