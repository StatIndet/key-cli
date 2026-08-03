#include "version_command.h"

#include "clavis_release.h"

#include <QJsonArray>

namespace {

QJsonObject versionObject()
{
    return {
        {QStringLiteral("product"), QStringLiteral("clavis-key")},
        {QStringLiteral("release"), QStringLiteral(CLAVIS_RELEASE)},
        {QStringLiteral("commit"), QStringLiteral(CLAVIS_COMMIT)},
        {QStringLiteral("buildTime"), QStringLiteral(CLAVIS_BUILD_TIME)},
        {QStringLiteral("channel"), QStringLiteral(CLAVIS_CHANNEL)},
        {QStringLiteral("protocols"),
         QJsonObject{
             {QStringLiteral("core"), 1},
             {QStringLiteral("clipboard"), 2},
             {QStringLiteral("shell"), 1},
         }},
        {QStringLiteral("features"),
         QJsonArray{
             QStringLiteral("clipboard.inspect"),
             QStringLiteral("clipboard.preview"),
             QStringLiteral("clipboard.mime-restore"),
             QStringLiteral("clipboard.mime-aware-store"),
             QStringLiteral("keytop.delegated"),
         }},
        {QStringLiteral("dataSchemas"),
         QJsonObject{
             {QStringLiteral("config"), 1},
             {QStringLiteral("manifest"), 1},
             {QStringLiteral("profile"), 1},
         }},
        {QStringLiteral("dependencyManifest"), 1},
    };
}

} // namespace

CommandResult VersionCommand::run(const QStringList &arguments) const
{
    const bool jsonRequested = arguments.contains(QStringLiteral("--json"));
    for (const QString &argument : arguments) {
        if (argument == QStringLiteral("--json"))
            continue;
        return {
            2,
            jsonRequested,
            {{QStringLiteral("ok"), false},
             {QStringLiteral("error"),
              QJsonObject{{QStringLiteral("code"), QStringLiteral("usage_error")},
                          {QStringLiteral("message"),
                           QStringLiteral("Unknown version option: %1").arg(argument)}}}},
            QStringLiteral("Unknown version option: %1").arg(argument),
            true,
        };
    }

    const QJsonObject object = versionObject();
    return {
        0,
        jsonRequested,
        object,
        QStringLiteral("key %1 (%2)").arg(
            QStringLiteral(CLAVIS_RELEASE), QStringLiteral(CLAVIS_COMMIT)),
        false,
    };
}
