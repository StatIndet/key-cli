#pragma once

#include "command_result.h"

#include <QStringList>

class ManagementCommand {
public:
    CommandResult run(const QString &command, const QStringList &arguments) const;
};
