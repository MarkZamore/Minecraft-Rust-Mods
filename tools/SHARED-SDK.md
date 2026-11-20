# Сборка Rust Mods для общего Horizon

Minecraft Rust Edition и Minecraft Rust Mods используют один выпуск общего Horizon SDK. У сборки Rust Mods есть собственные DLL модов, замены функций, пакеты и `native/native-mods.json`. Идентификатор ABI связывает их с точным SHA-256 клиента и версией компилятора. Новая сборка с другими модами выпускает свой каталог `native` для той же версии Horizon.

Запускайте команды из корня LANMinecraft. Используйте зарегистрированный активный каталог `artifacts/agent-runs/<task-id>`. Перед началом проверьте бюджет командой `python tools/workspace_storage.py --check`. Нужны проверенный снимок исходников Horizon Edition, подписанный архив исходников, `source/native` Steam-транспорта и проверенные архивы модов с `archive-inspection.json` из штатного шага приёмки Rust Mods. `horizon-mixins.exe` берите с соседним `source.json` из штатного кэша сборки модов. Для сборки Windows GNU путь к проверенному MinGW `bin` должен быть в `PATH` процесса.

```powershell
$Edition = 'LocalSources/Minecraft Rust Edition'
$Mods = 'LocalSources/Minecraft Rust Mods'
# $Run, $BaseSource, $SourceArchive, $SteamTransport, $Intake, $Mixins and $MingwBin
# are the paths from the registered build and verified intake.
python -B "$Mods/tools/shared_sdk_pack.py" --base-pack $Edition --base-source $BaseSource `
  --source-archive $SourceArchive --steam-transport $SteamTransport --out "$Run/mod-compat-seams"
python -B "$Mods/tools/shared_sdk_mod_inputs.py" --archives "$Intake/archives" `
  --inspection "$Intake/archive-inspection.json" --out "$Run/mod-inputs"
python -B "$Edition/integration/shared-native/build_sdk.py" --source $BaseSource `
  --source-archive $SourceArchive --steam-transport $SteamTransport --run $Run `
  --seams "$Run/mod-compat-seams/seams.json" --export-patch-source
python -B "$Mods/tools/shared_sdk_compile.py" --edition $Edition --inputs "$Run/mod-inputs" `
  --sdk "$Run/shared-sdk" --receipt "$Run/shared-sdk-build.json" `
  --sdk-stage "$Run/shared-sdk-cargo/prebuilt/stage" --patch-source "$Run/shared-sdk-patch-source" `
  --host-target "$Run/shared-sdk-cargo/prebuilt/tools" --mixins $Mixins --mingw-bin $MingwBin `
  --out "$Run/mod-sidecars"
```

Передайте `$Run/mod-sidecars/pack/native` упаковщику Rust Mods. Общий рантайм выпускается отдельно из того же проверенного SDK. Проверьте, что записи `native-mods.json` совпадают с файлами DLL, манифестом сборки и идентификатором ABI общего пакета. `mod-sidecar-receipt.json` содержит пути и SHA-256 собранных модов для сопоставления с фактически загруженным составом. Не помещайте общий EXE в архив Rust Mods.

При обновлении модов пересоберите проекцию из новой утверждённой версии `mod-input-lock.json`, решений о совместимости и `mod-selection.json`. Они определяют активный состав и повторно применяемые исправления. Если обновляется сам Horizon, заново проверьте совместимые точки вызова, классификацию клиентских и сетевых модов и установки замен функций. Инструмент останавливает подготовку при несовпадении исходных файлов, схемы манифеста, версии Rust или идентичности SDK. Исправления по результатам такой проверки вносите в исходные адаптации или нейтральные точки SDK.
