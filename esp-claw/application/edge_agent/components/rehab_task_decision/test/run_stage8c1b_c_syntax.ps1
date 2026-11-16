[CmdletBinding()]
param(
    [string]$HistoricalSourceRoot = ""
)

$ErrorActionPreference = "Stop"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$repo = (Resolve-Path (Join-Path $app "..\..")).Path
$idf = "C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4"
$compilerBin = "C:\esp\v5.5.4\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin"
$compiler = "xtensa-esp32s3-elf-gcc"
$nm = "xtensa-esp32s3-elf-nm"
$p4CompilerBin = "C:\esp\v5.5.4\tools\riscv32-esp-elf\esp-14.2.0_20260121\riscv32-esp-elf\bin"
$p4Compiler = Join-Path $p4CompilerBin "riscv32-esp-elf-gcc.exe"
$p4Nm = Join-Path $p4CompilerBin "riscv32-esp-elf-nm.exe"
if (-not (Test-Path -LiteralPath $idf) -or
    -not (Test-Path -LiteralPath (Join-Path $compilerBin "$compiler.exe")) -or
    -not (Test-Path -LiteralPath $p4Compiler) -or
    -not (Test-Path -LiteralPath $p4Nm)) {
    throw "Stage 8C-1B host syntax toolchain is unavailable"
}

$originalPath = $env:PATH
$env:PATH = "$compilerBin;$originalPath"
$sizeObject = Join-Path $env:TEMP "rehab_task_decision_sizes.o"
$p4SizeObject = Join-Path $env:TEMP "rehab_task_decision_p4_sizes.o"
$coordinatorObject = Join-Path $env:TEMP "rehab_task_decision_coordinator_sizes.o"
$stackUsageDir = Join-Path $env:TEMP "rehab_task_decision_stack_usage"
try {
    $includeDirs = @(
        (Join-Path $PSScriptRoot "host_syntax"),
        (Join-Path $app "main"),
        (Join-Path $repo "components\lua_modules\lua_module_image\src"),
        (Join-Path $app "managed_components\georgik__lua\lua"),
        (Join-Path $app "managed_components\espressif__esp_board_manager\private_inc"),
        (Join-Path $idf "components\freertos\FreeRTOS-Kernel\include"),
        (Join-Path $idf "components\freertos\FreeRTOS-Kernel\portable\xtensa\include"),
        (Join-Path $idf "components\freertos\FreeRTOS-Kernel\portable\xtensa\include\freertos"),
        (Join-Path $idf "components\freertos\config\include"),
        (Join-Path $idf "components\freertos\config\include\freertos"),
        (Join-Path $idf "components\freertos\config\xtensa\include"),
        (Join-Path $idf "components\freertos\esp_additions\include"),
        (Join-Path $idf "components\esp_common\include"),
        (Join-Path $idf "components\esp_system\include"),
        (Join-Path $idf "components\esp_hw_support\include"),
        (Join-Path $idf "components\esp_timer\include"),
        (Join-Path $idf "components\log\include"),
        (Join-Path $idf "components\heap\include"),
        (Join-Path $idf "components\soc\include"),
        (Join-Path $idf "components\soc\esp32s3\include"),
        (Join-Path $idf "components\soc\esp32s3\register"),
        (Join-Path $idf "components\hal\include"),
        (Join-Path $idf "components\hal\esp32s3\include"),
        (Join-Path $idf "components\xtensa\include"),
        (Join-Path $idf "components\xtensa\esp32s3\include"),
        (Join-Path $idf "components\newlib\platform_include"),
        (Join-Path $idf "components\esp_libc\platform_include"),
        (Join-Path $idf "components\mbedtls\port\include"),
        (Join-Path $idf "components\mbedtls\mbedtls\include"),
        (Join-Path $idf "components\json\cJSON"),
        (Join-Path $idf "components\esp_rom\include"),
        (Join-Path $idf "components\console"),
        (Join-Path $idf "components\console\include"),
        (Join-Path $idf "components\lwip\include"),
        (Join-Path $idf "components\lwip\lwip\src\include"),
        (Join-Path $idf "components\lwip\port\include"),
        (Join-Path $idf "components\esp_netif\include"),
        (Join-Path $idf "components\esp_event\include"),
        (Join-Path $idf "components\nvs_flash\include"),
        (Join-Path $idf "components\esp_wifi\include"),
        (Join-Path $idf "components\esp_driver_gpio\include"),
        (Join-Path $idf "components\esp_driver_i2c\include"),
        (Join-Path $idf "components\esp_driver_ledc\include"),
        (Join-Path $idf "components\esp_driver_uart\include"),
        (Join-Path $idf "components\esp_partition\include")
    )
    $includeDirs += Get-ChildItem -LiteralPath `
        (Join-Path $app "components"), `
        (Join-Path $repo "components"), `
        (Join-Path $app "managed_components") `
        -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "include" -and
            $_.FullName -notmatch "[\\/](test|tests|test_apps|examples)[\\/]"
        } |
        ForEach-Object FullName
    $includeDirs += Get-ChildItem -LiteralPath (Join-Path $app "components") `
        -Directory -ErrorAction SilentlyContinue |
        ForEach-Object FullName
    $boardManager = Join-Path $app "managed_components\espressif__esp_board_manager"
    $includeDirs += Get-ChildItem -LiteralPath `
        (Join-Path $boardManager "devices"), `
        (Join-Path $boardManager "peripherals") `
        -Directory -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object FullName
    $includeDirs = $includeDirs | Select-Object -Unique

    $common = @(
        "-fsyntax-only",
        "-std=gnu11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-D_GNU_SOURCE",
        "-DESP_PLATFORM",
        "-DCONFIG_IDF_TARGET_ESP32S3=1",
        "-DCONFIG_REHAB_PERSON_FOLLOW_ENABLED=0",
        "-DCONFIG_REHAB_AUDIO_OUTPUT_CODEC_BOOST_DB_X10=0",
        "-DCONFIG_APP_CLAW_CAP_IM_WECHAT=0",
        "-Wno-implicit-function-declaration",
        "-Wno-int-conversion",
        "-Wno-pointer-to-int-cast",
        "-Wno-int-to-pointer-cast"
    )
    foreach ($includeDir in $includeDirs) {
        $common += "-I"
        $common += $includeDir
    }
    $sources = @(
        (Join-Path $app "components\rehab_task_decision\src\rehab_decision_trace.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_policy.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_preparation.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_llm_input_adapter.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_scoring.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_context.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_rules.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_validate.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_selected_task.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_details.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_details_binding.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_target_resolver.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_coordinator.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_confirmed_start.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_coordinator_capabilities.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_coordinator_console.c"),
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_core.c"),
        (Join-Path $app "components\rehab_ui\src\rehab_ui_task_decision_lua.c"),
        (Join-Path $app "components\rehab_integration\src\rehab_capabilities.c"),
        (Join-Path $app "main\main.c")
    )
    foreach ($source in $sources) {
        & $compiler @common $source
        if ($LASTEXITCODE -ne 0) {
            throw "C -fsyntax-only failed: $source"
        }
        Write-Output "C_SYNTAX_PASS $source"
    }
    $objectCommon = @($common | Where-Object { $_ -ne "-fsyntax-only" })
    & $compiler @objectCommon -c `
        (Join-Path $PSScriptRoot "test_stack_size_contract.c") `
        -o $sizeObject
    if ($LASTEXITCODE -ne 0) {
        throw "Target ABI size contract compilation failed"
    }
    & $nm -S --size-sort $sizeObject | Where-Object {
        $_ -match "rehab_size_"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Target ABI size symbol inspection failed"
    }
    & $p4Compiler -std=gnu11 -Wall -Wextra -Werror -c `
        -I (Join-Path $PSScriptRoot "stack_size_target_stubs") `
        -I (Join-Path $app "components\rehab_task_decision\include") `
        (Join-Path $PSScriptRoot "test_stack_size_contract.c") `
        -o $p4SizeObject
    if ($LASTEXITCODE -ne 0) {
        throw "ESP32-P4 ABI size contract compilation failed"
    }
    & $p4Nm -S --size-sort $p4SizeObject | Where-Object {
        $_ -match "rehab_size_"
    } | ForEach-Object { "P4_ABI_SIZE $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "ESP32-P4 ABI size symbol inspection failed"
    }

    $historical = $HistoricalSourceRoot -ne ""
    if ($historical) {
        $HistoricalSourceRoot = (Resolve-Path -LiteralPath $HistoricalSourceRoot).Path
    }
    $p4IncludeDirs = @((Join-Path $PSScriptRoot "host_syntax_p4"))
    if ($historical) {
        $p4IncludeDirs = @(
            (Join-Path $HistoricalSourceRoot `
                "application\edge_agent\components\rehab_task_decision\include"),
            (Join-Path $app "components\rehab_task_decision\src")
        ) + $p4IncludeDirs
    }
    $p4IncludeDirs += @($includeDirs | Where-Object {
        $_ -notmatch "host_syntax" -and
        $_ -notmatch "[\\/]xtensa([\\/]|$)" -and
        $_ -notmatch "[\\/]esp32s3([\\/]|$)"
    }) + @(
        (Join-Path $idf "components\freertos\FreeRTOS-Kernel\portable\riscv\include"),
        (Join-Path $idf "components\freertos\FreeRTOS-Kernel\portable\riscv\include\freertos"),
        (Join-Path $idf "components\freertos\config\riscv\include"),
        (Join-Path $idf "components\soc\esp32p4\include"),
        (Join-Path $idf "components\soc\esp32p4\register\hw_ver3"),
        (Join-Path $idf "components\hal\esp32p4\include"),
        (Join-Path $idf "components\riscv\include")
    )
    $p4Common = @(
        "-std=gnu11", "-Os", "-Wall", "-Wextra", "-Werror",
        "-D_GNU_SOURCE", "-DESP_PLATFORM", "-DCONFIG_IDF_TARGET_ESP32P4=1",
        "-DCONFIG_REHAB_PERSON_FOLLOW_ENABLED=0",
        "-DCONFIG_REHAB_AUDIO_OUTPUT_CODEC_BOOST_DB_X10=0",
        "-DCONFIG_APP_CLAW_CAP_IM_WECHAT=0",
        "-Wno-implicit-function-declaration", "-Wno-int-conversion",
        "-Wno-pointer-to-int-cast", "-Wno-int-to-pointer-cast"
    )
    foreach ($includeDir in ($p4IncludeDirs | Select-Object -Unique)) {
        $p4Common += "-I"
        $p4Common += $includeDir
    }
    New-Item -ItemType Directory -Path $stackUsageDir -Force | Out-Null
    $stackTargets = if ($historical) {
        @{
            "lua_task_decision_start" = Join-Path $HistoricalSourceRoot "application\edge_agent\components\rehab_ui\src\rehab_ui_task_decision_lua.c"
            "rehab_task_decision_start_confirmed" = Join-Path $HistoricalSourceRoot "application\edge_agent\components\rehab_task_decision\src\rehab_task_decision_confirmed_start.c"
            "rehab_task_decision_coordinator_start" = Join-Path $HistoricalSourceRoot "application\edge_agent\components\rehab_task_decision\src\rehab_task_decision_coordinator.c"
        }
    } else {
        @{
            "lua_task_decision_start" = Join-Path $app "components\rehab_ui\src\rehab_ui_task_decision_lua.c"
            "rehab_task_decision_start_confirmed" = Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_confirmed_start.c"
            "rehab_task_decision_coordinator_start" = Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_coordinator.c"
        }
    }
    foreach ($functionName in $stackTargets.Keys) {
        $object = Join-Path $stackUsageDir "$functionName.o"
        $stackFlags = @($p4Common)
        if ($functionName -eq "rehab_task_decision_coordinator_start") {
            $stackFlags += "-DREHAB_TASK_DECISION_SIZE_PROBE=1"
        }
        & $p4Compiler @stackFlags -Wno-unused-function -fstack-usage -c `
            $stackTargets[$functionName] `
            -o $object
        if ($LASTEXITCODE -ne 0) {
            throw "ESP32-P4 stack-usage compilation failed: $functionName"
        }
        $usageFile = Join-Path $stackUsageDir "$functionName.su"
        $usage = Get-Content -LiteralPath $usageFile | Where-Object {
            $_ -match "\b$functionName\b"
        }
        if (@($usage).Count -ne 1) {
            throw "Missing ESP32-P4 stack usage entry: $functionName"
        }
        $fields = $usage -split "`t"
        $bytes = [int]$fields[1]
        if (-not $historical -and $bytes -gt 512) {
            throw "ESP32-P4 stack frame exceeds 512 bytes: $functionName=$bytes"
        }
        $label = if ($historical) {
            "P4_STACK_USAGE_HISTORICAL"
        } else {
            "P4_STACK_USAGE"
        }
        Write-Output "$label $functionName $bytes bytes"
    }
    $p4CoordinatorObject = Join-Path $stackUsageDir `
        "rehab_task_decision_coordinator_start.o"
    & $p4Nm -S --size-sort $p4CoordinatorObject | Where-Object {
        $_ -match "rehab_size_formal_scene"
    } | ForEach-Object { "P4_ABI_SIZE_FORMAL_SCENE $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "ESP32-P4 formal_scene size symbol inspection failed"
    }
    & $compiler @objectCommon -Wno-unused-function `
        -DREHAB_TASK_DECISION_SIZE_PROBE=1 -c `
        (Join-Path $app "components\rehab_task_decision\src\rehab_task_decision_coordinator.c") `
        -o $coordinatorObject
    if ($LASTEXITCODE -ne 0) {
        throw "formal_scene size probe compilation failed"
    }
    & $nm -S --size-sort $coordinatorObject | Where-Object {
        $_ -match "rehab_size_formal_scene"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "formal_scene size symbol inspection failed"
    }
} finally {
    $env:PATH = $originalPath
    foreach ($artifact in @($sizeObject, $p4SizeObject, $coordinatorObject)) {
        if (Test-Path -LiteralPath $artifact) {
            Remove-Item -LiteralPath $artifact -Force
        }
    }
    foreach ($functionName in @(
        "lua_task_decision_start",
        "rehab_task_decision_start_confirmed",
        "rehab_task_decision_coordinator_start")) {
        foreach ($extension in @("o", "su")) {
            $artifact = Join-Path $stackUsageDir "$functionName.$extension"
            if (Test-Path -LiteralPath $artifact) {
                Remove-Item -LiteralPath $artifact -Force
            }
        }
    }
}
