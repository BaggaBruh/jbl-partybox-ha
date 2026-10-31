# Decompiling the JBL PartyBox APK

The decompiled sources aren't committed (they're JBL's code). To regenerate them into `decompiled/`:

1. Put the APK (`JBL_PartyBox-3.14.20.apk`, from an APK mirror or `adb pull` from a phone) in `apks/` (git ignores that folder).
2. Get a JADX jar that contains `jadx.api.JadxDecompiler`. The jadx CLI release
   (`jadx-<version>/lib/jadx-<version>-all.jar`) works; the original analysis used
   `jadx-headless-mcp-0.7.1-all.jar`, which bundles jadx-core.
3. With JDK 17+:

```bash
javac -cp path/to/jadx-all.jar -d tools/decompile tools/decompile/Dump.java
java -Xmx6g -cp "path/to/jadx-all.jar;tools/decompile" Dump apks/JBL_PartyBox-3.14.20.apk decompiled
```

(Use `:` instead of `;` in the classpath on Linux/macOS.)

Useful starting points in `decompiled/sources/com/harman/sdk/`: `utils/PacketFormat.java` (command
bytes), `command/*.java` (one class per command), `impl/*ControlImpl.java` (payload builders),
`command/CommandProcessor.java` (response parsing), `impl/connect/GattControllerImpl.java` (GATT setup).
