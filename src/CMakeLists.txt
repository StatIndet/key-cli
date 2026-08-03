set(KEY_CLI_RELEASE "development" CACHE STRING "key-cli release")
set(KEY_CLI_COMMIT "unknown" CACHE STRING "key-cli commit")
set(KEY_CLI_BUILD_TIME "unknown" CACHE STRING "key-cli build time")
set(KEY_CLI_CHANNEL "stable" CACHE STRING "key-cli channel")
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/clavis_release.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/clavis_release.h"
    @ONLY
)

file(GLOB_RECURSE KEY_CLI_RECORDING_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/core/recording/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/core/recording/*.h"
)

add_library(KeyCliRuntime STATIC
    core/runtime/clavis_paths.h
    core/runtime/clavis_paths.cpp
)
set_target_properties(KeyCliRuntime PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(KeyCliRuntime PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/core"
)
target_link_libraries(KeyCliRuntime PUBLIC Qt6::Core Qt6::Network)

add_library(KeyCliRecording STATIC ${KEY_CLI_RECORDING_SOURCES})
set_target_properties(KeyCliRecording PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(KeyCliRecording PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/core"
)
target_link_libraries(KeyCliRecording PUBLIC
    KeyCliRuntime
    Qt6::Core
    Qt6::Gui
    Qt6::Network
)

add_library(KeyCliNiri STATIC
    core/niri/niri_cast_parser.h
    core/niri/niri_cast_parser.cpp
    core/niri/niri_ipc_client.h
    core/niri/niri_ipc_client.cpp
    core/niri/niri_types.h
)
set_target_properties(KeyCliNiri PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(KeyCliNiri PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/core/niri"
    "${CMAKE_CURRENT_SOURCE_DIR}/core"
)
target_link_libraries(KeyCliNiri PUBLIC Qt6::Core Qt6::Network)

add_executable(key
    main.cpp
    command_result.h
    command_router.h
    command_router.cpp
    commands/audio_command.h
    commands/audio_command.cpp
    commands/cast_command.h
    commands/cast_command.cpp
    commands/clipboard_command.h
    commands/clipboard_command.cpp
    commands/doctor_command.h
    commands/doctor_command.cpp
    commands/management_command.h
    commands/management_command.cpp
    commands/record_command.h
    commands/record_command.cpp
    commands/top_command.h
    commands/top_command.cpp
    commands/version_command.h
    commands/version_command.cpp
    commands/weather_command.h
    commands/weather_command.cpp
)
set_target_properties(key PROPERTIES
    OUTPUT_NAME key
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)
target_include_directories(key PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/core"
    "${CMAKE_CURRENT_BINARY_DIR}/generated"
)
target_link_libraries(key PRIVATE
    KeyCliRuntime
    KeyCliRecording
    KeyCliNiri
    Qt6::Core
    Qt6::Gui
    Qt6::Network
)

install(TARGETS key
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../packaging/clavis-manager.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../packaging/clavis_paths.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/../packaging/components.py"
    DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/key"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE WORLD_READ WORLD_EXECUTE
)
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/../packaging/weather.py"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/key-cli"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE WORLD_READ WORLD_EXECUTE
)
