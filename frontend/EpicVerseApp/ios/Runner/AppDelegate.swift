import Flutter
import UIKit
import IOSSecuritySuite

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // Finding #4 (root/jailbreak detection bypass) hardening.
  // Native instrumentation signals from IOSSecuritySuite (already bundled via
  // flutter_jailbreak_detection), alongside its existing amIJailbroken() check
  // which the Dart side still calls through the plugin.
  //
  // integritySignals returns:
  //   strong:   specific indicators that justify blocking (Frida/Cycript
  //             library injected into the process, frida-server file, Frida's
  //             default port 27042)
  //   weak:     indicators too generic to block on (any other local port the
  //             library probes: 22, 44, 4444; the experimental P_SELECT flag)
  //   debugged: a debugger is attached — evaluated only when Dart asks for it
  //             (release builds), never on the simulator
  // Only short fixed codes are returned, never the library's free-text messages.
  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)

    guard let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "IntegrityChannel") else {
      return
    }
    let channel = FlutterMethodChannel(name: "epicverse/integrity", binaryMessenger: registrar.messenger())
    channel.setMethodCallHandler { call, result in
      switch call.method {
      case "amIDebugged":
        result(IOSSecuritySuite.amIDebugged())
      case "integritySignals":
        let evaluateDebugger = (call.arguments as? [String: Any])?["evaluateDebugger"] as? Bool ?? false
        // Off the main thread so launch and the UI are never blocked.
        DispatchQueue.global(qos: .userInitiated).async {
          let status = IOSSecuritySuite.amIReverseEngineeredWithFailedChecks()
          var strong: [String] = []
          var weak: [String] = []
          for failed in status.failedChecks {
            switch failed.check {
            case .dyld:
              strong.append("dyld")
            case .existenceOfSuspiciousFiles:
              strong.append("frida_server_file")
            case .openedPorts:
              // The library probes 27042 first, so any other message means
              // 27042 was closed and a generic port (22/44/4444) answered.
              if failed.failMessage == "Port 27042 is open" {
                strong.append("frida_port")
              } else {
                weak.append("local_port")
              }
            case .pSelectFlag:
              weak.append("p_select")
            default:
              weak.append("other")
            }
          }
          var debugged = false
          #if !targetEnvironment(simulator)
          if evaluateDebugger {
            debugged = IOSSecuritySuite.amIDebugged()
          }
          #endif
          DispatchQueue.main.async {
            result(["strong": strong, "weak": weak, "debugged": debugged])
          }
        }
      default:
        result(FlutterMethodNotImplemented)
      }
    }
  }
}
