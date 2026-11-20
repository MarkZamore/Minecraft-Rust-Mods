# Подготовка Rust Mods для общего рантайма

Для подготовки нужны принятый полный кандидат Rust Mods и его точный
`distribution-lock.json`, результат сборки SDK-модулей и дистрибутив общего
рантайма из `LANMinecraft/tools/native_runtime_release.py stage-runtime`.
Оба кандидата размещаются в зарегистрированных активных стендах LANMinecraft.

```powershell
python -B tools/stage_shared_pack.py `
    --static-candidate $acceptedStaticCandidate --static-lock $acceptedStaticLock `
    --sidecars $sdkModOutput --runtime $runtimeDist --overlay $newOverlay `
    --common-candidate $commonCandidate --qa-view $newFullQaView
```

`$sdkModOutput` содержит `pack/native` и `mod-sidecar-receipt.json`.
Инструмент сверяет размеры и SHA‑256 принятого состава и общего архива,
проверяет точный состав модов и ABI библиотек. Манифесты нативных пакетов
привязываются к общему ABI. Данные модов, лицензии, название сборки,
сетевой вариант и настройки берутся из принятого кандидата. Новый
`portable-pack.json` закрепляет общий архив, а `native/native-mods.json`
указывает библиотеки и метаданные собранных SDK-модулей.

`--common-candidate` и `--qa-view` вместе создают полную раскладку для
старых инструментов проверки файлов. Общие файлы, библиотеки и крупные
неизменяемые ресурсы могут использовать жёсткие ссылки внутри активных
стендов. Дескрипторы, конфигурация и библиотечные метаданные получают
собственные копии. Для проверки штатного запуска передайте общий рантайм
и `$newOverlay` в `Minecraft Rust Edition/tools/smoke_shared_native.py`.

`shared-composition.json` фиксирует происхождение принятого состава,
точные общие и индивидуальные файлы, а также физический источник каждого
мода в новой раскладке. После игровой приёмки используйте его для обновления
библиотечных метаданных и lock выпуска, затем подготовьте архивы по
`LANMinecraft/tools/NATIVE-RUNTIME-RELEASE.md`.
