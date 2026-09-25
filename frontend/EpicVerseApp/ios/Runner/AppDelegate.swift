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

  // Finding #4 (root/jailbreak detection bypass) local hardening.
  // Exposes IOSSecuritySuite.amIDebugged() — already a bundled dependency
  // via flutter_jailbreak_detection, not previously called by the app —
  // as an additional signal alongside the existing amIJailbroken() check.
  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)

    let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "IntegrityChannel")
    let channel = FlutterMethodChannel(name: "epicverse/integrity", binaryMessenger: registrar.messenger())
    channel.setMethodCallHandler { call, result in
      if call.method == "amIDebugged" {
        result(IOSSecuritySuite.amIDebugged())
      } else {
        result(FlutterMethodNotImplemented)
      }
    }
  }
}
