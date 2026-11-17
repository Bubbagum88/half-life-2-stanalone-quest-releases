# Quest 1 patches (work in progress)

This folder is not part of the official releases. It patches the official
`hl2quest-vr.apk` **1.02** so the game can run on the original **Meta Quest 1**
(Snapdragon 835 / Adreno 540, Horizon OS v50, Android 10). Only Portal has been tried so far.

**Status: not playable yet.** The game now gets past startup and loads Portal's
game libraries, then stops because the shaders don't load (see "Next blocker").

## What the patch changes

`quest1_patch_apk.py` takes the official APK and writes a patched one. It
checks every byte it replaces, so it refuses to run on any other APK.

| # | File in the APK | Problem on Quest 1 | Change | Tested on headset |
|---|---|---|---|---|
| 1 | `assets/hl2quest_launcher_web/launcher.js` | The menu is a WebView page using `?.` / `??` (Chrome 80+). The Quest 1 WebView is Chrome 74.0.3729, so the script fails to parse and the menu is blank. | 6 expressions rewritten as plain checks. | Yes, menu works |
| 2 | `lib/arm64-v8a/libsourcevr.so` (file offset `0x16304`) | Requests OpenXR 1.1.53; the v50 runtime only accepts 1.0, so `xrCreateInstance` returns `-4`. | Requested version is now 1.0.0. The library only uses 1.0 functions. | Yes, "OpenXR instance created" |
| 3 | `lib/arm64-v8a/libdxvk_d3d9.so` (DXVK 1.9.2) | `Required Vulkan extension VK_KHR_image_format_list not supported`. D3D9 also forces `geometryShader`, `multiViewport`, `textureCompressionBC`, `occlusionQueryPrecise`, all 0 on the Adreno 540 (driver 512.555, Vulkan 1.1.128). | `image_format_list` marked Optional in `DxvkAdapter::createDevice`. The four features are no longer forced in `D3D9DeviceEx::GetDeviceFeatures`. 4 instructions. | Yes, Vulkan device and swapchain created |

The APK is re-signed with our own key (v1 is enough, targetSdk 24). It
can't install over the official app, so uninstall that first. Your saved
games in `/sdcard/srceng` are not touched.

```
python3 quest1/quest1_patch_apk.py hl2quest-vr-1.02.apk hl2quest-vr-1.02-quest1.apk ./q1key
```
Reuse the same key folder for later builds so they install with `adb install -r`.

## Setup notes for Quest 1

- **Storage permission:** a fresh install of the patched APK has no storage
  permission, and the game then says it can't find the files. Grant it from adb:
  ```
  adb shell pm grant com.valvesoftware.source.vrtest android.permission.WRITE_EXTERNAL_STORAGE
  adb shell pm grant com.valvesoftware.source.vrtest android.permission.READ_EXTERNAL_STORAGE
  ```
- **Starting a game:** on Quest 1, the launcher's Play button starts the game
  from a 2D panel, and the system then closes it, leaving a `:game` process
  without a window ("Quit the game through its menu first"). For now, start it
  from adb instead (PowerShell: keep the single quotes and the `\ ` escapes):
  ```
  adb shell am force-stop com.valvesoftware.source.vrtest
  adb shell 'am start -n com.valvesoftware.source.vrtest/org.libsdl.app.SDLActivity --es gamedir portal --es argv -vr\ -novid --ez legacy_gles false'
  ```
- `legacy_gles true` makes no difference for Portal: it always uses DXVK.

## Next blocker

```
Error: Material "debug/debugempty" uses unknown shader "UnlitGeneric"
```
The material system loads `stdshader_dx6`..`dx8` (not shipped on Android) but
never `libstdshader_dx9.so`, even with `-dxlevel 90`. So the engine's maximum
DX level comes out below 90 with the Quest 1's capabilities. The likeliest
cause is the missing DXT/BC texture support (patch 3 now reports it truthfully).
Not confirmed yet.

## Still open after that

- DXT textures: the Adreno 540 can't sample BC formats, so Source has to
  convert them at load time, or the PC cache builder has to write uncompressed ones.
- DXVK's internal layered-image copies use a geometry shader when
  `VK_EXT_shader_viewport_index_layer` is missing (Quest 1 has neither).
- Starting games from the launcher on Quest 1.
- Performance on the Snapdragon 835 is unknown.
