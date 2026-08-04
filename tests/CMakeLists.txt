qt_add_executable(key_cli_router_tests
    router_test.cpp
)
target_link_libraries(key_cli_router_tests PRIVATE Qt6::Core Qt6::Test)
target_include_directories(key_cli_router_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../src")
add_test(NAME key_cli_router_tests COMMAND key_cli_router_tests)

find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_test(
    NAME key_cli_weather_tests
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/test_weather.py"
)
add_test(
    NAME key_cli_component_provider_tests
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/test_components.py"
)
add_test(
    NAME key_cli_release_manager_tests
    COMMAND Python3::Interpreter
            "${CMAKE_CURRENT_SOURCE_DIR}/test_release_manager.py"
)

find_program(BASH_EXECUTABLE bash REQUIRED)
add_test(
    NAME key_cli_theme_command_tests
    COMMAND "${BASH_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/test_theme_command.sh"
            "$<TARGET_FILE:key>"
)
