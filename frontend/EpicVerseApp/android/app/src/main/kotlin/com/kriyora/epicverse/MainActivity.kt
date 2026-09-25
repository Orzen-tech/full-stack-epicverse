package com.kriyora.epicverse

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * Finding #4 (root/jailbreak detection bypass) local hardening.
 *
 * Exposes a single method reading `TracerPid` from `/proc/self/status` —
 * a kernel-level signal of whether this process is currently ptrace-
 * attached, independent of RootBeer's Java-level checks. See the
 * security investigation for this finding's documented scope and
 * limitations (notably: does not catch an attach-then-detach pattern).
 */
class MainActivity : FlutterActivity() {
    private val INTEGRITY_CHANNEL = "epicverse/integrity"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, INTEGRITY_CHANNEL)
            .setMethodCallHandler { call, result ->
                if (call.method == "tracerPid") {
                    result.success(readTracerPid())
                } else {
                    result.notImplemented()
                }
            }
    }

    /** Returns the TracerPid value (0 = not traced), or -1 if it could not be read. */
    private fun readTracerPid(): Int {
        return try {
            File("/proc/self/status").readLines()
                .firstOrNull { it.startsWith("TracerPid:") }
                ?.substringAfter(":")
                ?.trim()
                ?.toIntOrNull()
                ?: -1
        } catch (e: Exception) {
            // Never let a read failure propagate as a crash — the Dart
            // side treats -1 as "unknown", never as "compromised".
            -1
        }
    }
}
