#pragma once

#include "command_result.h"

#include <QStringList>

class WeatherCommand {
public:
    CommandResult run(const QStringList &arguments) const;
};

