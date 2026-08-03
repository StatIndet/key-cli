#include "command_router.h"

#include "commands/audio_command.h"
#include "commands/cast_command.h"
#include "commands/clipboard_command.h"
#include "commands/doctor_command.h"
#include "commands/management_command.h"
#include "commands/record_command.h"
#include "commands/top_command.h"
#include "commands/version_command.h"
#include "commands/weather_command.h"
#include "recording/recording_types.h"

#include <QCoreApplication>

using namespace Clavis::Recording;

CommandResult CommandRouter::route(const QStringList &arguments) const
{
    if (arguments.isEmpty() || arguments.first() == QStringLiteral("--help")
        || arguments.first() == QStringLiteral("-h")) {
        return {Success, false, {}, helpText(), false};
    }
    if (arguments.first() == QStringLiteral("--version")
        || arguments.first() == QStringLiteral("-v")) {
        return VersionCommand().run(arguments.mid(1));
    }

    const QString command = arguments.first();
    const QStringList rest = arguments.mid(1);
    if (command == QStringLiteral("version"))
        return VersionCommand().run(rest);
    if (command == QStringLiteral("doctor")
        && !rest.isEmpty()
        && rest.first() == QStringLiteral("legacy")) {
        return ManagementCommand().run(command, rest);
    }
    if (command == QStringLiteral("doctor"))
        return DoctorCommand().run(rest);
    if (command == QStringLiteral("audio"))
        return AudioCommand().run(rest);
    if (command == QStringLiteral("record"))
        return RecordCommand().run(rest);
    if (command == QStringLiteral("cast"))
        return CastCommand().run(rest);
    if (command == QStringLiteral("clipboard"))
        return ClipboardCommand().run(rest);
    if (command == QStringLiteral("top"))
        return TopCommand().run(rest);
    if (command == QStringLiteral("weather"))
        return WeatherCommand().run(rest);
    if (command == QStringLiteral("shell")
        || command == QStringLiteral("ipc")
        || command == QStringLiteral("rollback")
        || command == QStringLiteral("uninstall")
        || command == QStringLiteral("migrate")
        || command == QStringLiteral("setup")
        || command == QStringLiteral("release")
        || command == QStringLiteral("update")
        || command == QStringLiteral("install")
        || command == QStringLiteral("component")) {
        return ManagementCommand().run(command, rest);
    }
    return usageError(QStringLiteral("Unknown command: %1").arg(command),
                      arguments.contains(QStringLiteral("--json")));
}

QString CommandRouter::helpText()
{
    return QStringLiteral(
        "Clavis Shell command line interface\n"
        "\n"
        "Usage:\n"
        "  key [--help] [--version]\n"
        "  key version [--json]\n"
        "  key shell [--replace] [--foreground] [--no-duplicate] "
        "[-- QUICKSHELL_OPTIONS...]\n"
        "  key shell --dev [--native] [--source PATH] [--replace] "
        "[--foreground] [--no-duplicate]\n"
        "  key shell logs [--follow] [--mode release|dev|dev-native]\n"
        "  key ipc [list|show]\n"
        "  key ipc call TARGET METHOD [ARGUMENTS...]\n"
        "  key doctor [--json] [--output DIRECTORY]\n"
        "  key doctor cpu-power [--json]\n"
        "  key audio start --source mic|system [--output DIRECTORY] [--json]\n"
        "  key audio status [--json]\n"
        "  key audio stop [--json]\n"
        "  key record start --type video|gif --geometry WIDTHxHEIGHT+X+Y [options]\n"
        "  key record status [--json]\n"
        "  key record stop [--json]\n"
        "  key cast list [--json]\n"
        "  key cast status [--json]\n"
        "  key clipboard list --format json [--limit N]\n"
        "  key clipboard inspect|preview ID --format json\n"
        "  key clipboard restore|delete ID [--format json]\n"
        "  key clipboard clear|status [--format json]\n"
        "  key clipboard watch\n"
        "  key top [--interval MILLISECONDS]\n"
        "  key weather [--json] [--refresh] [--ttl SECONDS] [--latitude LAT --longitude LON]\n"
        "  key install keytop|clavis-zsh-theme|clavis-fcitx5-theme [--source PATH]\n"
        "  key update COMPONENT\n"
        "  key component status [--json]\n"
        "  key uninstall COMPONENT\n"
        "  key rollback [RELEASE] [--dry-run]\n"
        "  key release list [--json]\n"
        "  key release remove RELEASE [--dry-run]\n"
        "  key update [--artifact PATH]\n"
        "  key uninstall [--dry-run] [--purge-cache] [--purge-config] [--purge-data]\n"
        "  key setup cpu-power [--disable] [--dry-run]\n"
        "  key doctor legacy [--json]\n"
        "  key migrate legacy [--dry-run]\n"
        "\n"
        "Recording options:\n"
        "  --type TYPE         video or gif (default: video)\n"
        "  --target TARGET     region (default: region)\n"
        "  --geometry REGION   required compositor-logical WIDTHxHEIGHT+X+Y\n"
        "  --audio SOURCE      none or system (default: none)\n"
        "  --fps NUMBER        capture rate from 1 to 240 (default: 60)\n"
        "  --output DIRECTORY  output directory\n"
        "  --json              stable machine-readable output\n"
        "\n"
        "Exit codes:\n"
        "  0 success, 1 runtime/TUI, 2 usage, 3 dependency/output\n"
        "  4 session conflict\n"
        "  5 state, 6 recorder start, 7 recorder stop, 8 post-process\n"
        "  11 niri unavailable\n");
}

CommandResult CommandRouter::usageError(const QString &message, bool jsonRequested)
{
    const RecordingError error = makeError(QStringLiteral("usage_error"), message);
    return {
        UsageError,
        jsonRequested,
        QJsonObject{
            {QStringLiteral("schemaVersion"), SchemaVersion},
            {QStringLiteral("command"), QStringLiteral("unknown")},
            {QStringLiteral("ok"), false},
            {QStringLiteral("error"), error.toJson()},
        },
        message + QStringLiteral("\n\n") + helpText(),
        true,
    };
}
