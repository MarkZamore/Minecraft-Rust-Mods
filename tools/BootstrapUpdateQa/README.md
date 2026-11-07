# Приёмка обновления bootstrap Rust Mods

Проверка использует настоящие `PortablePackSyncService`, `PackInstanceService` и WPF-контролы из указанного checkout LANMinecraft. Она устанавливает точную предыдущую поставку, обновляет bootstrap и данные происхождения базы, затем повторяет обновление. Проверяются все управляемые файлы, восемь контрольных личных файлов и оформление карточки с 40 модами. Мир и резервная копия представлены синтетическими метками. Игровая механика проверяется отдельно.

Входы `old-dist` и `new-dist` содержат `pack-manifest.json` и `assets/` с точными публичными или подготовленными архивами. Рабочие файлы должны находиться внутри одного зарегистрированного стенда `LANMinecraft/artifacts/agent-runs`. Техническая установка удаляется в `finally`. В `output/report.json` сохраняются ревизии, SHA256 сборки лаунчера и результаты.

```powershell
dotnet build tools/BootstrapUpdateQa/BootstrapUpdateQa.csproj --artifacts-path $build `
    -p:LauncherRoot=$launcherSource -p:SkipPortableIdentityAdapter=true
$probe = Get-ChildItem "$build/bin/BootstrapUpdateQa" -Recurse -Filter BootstrapUpdateQa.dll |
    Select-Object -First 1
dotnet $probe.FullName $workspace $oldDist $newDist $output
```

Для текущего обновления изменяются только `native/launch-rust-edition.exe` и `native/native-mods.json`. SHA256 клиента с 40 модами, контентного пакета, пресета и настроек поставки сохраняются. Проверка предназначена для этого перехода и завершится ошибкой при ином числе изменённых файлов.
