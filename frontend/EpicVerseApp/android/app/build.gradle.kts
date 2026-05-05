plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("dev.flutter.flutter-gradle-plugin")
    id("com.google.gms.google-services")
}

android {
    namespace = "com.kriyora.epicverse"
    compileSdk = 35 // Stick to 35 for stability with AGP 8.6.0
    
    // Build tools will be automatically selected based on compileSdk if omitted
    // buildToolsVersion = "35.0.0" 

    // speech_to_text requires NDK 28.2.13676358
    ndkVersion = "28.2.13676358"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    defaultConfig {
        applicationId = "com.kriyora.epicverse"
        minSdk = 26
        targetSdk = 35 // Match compileSdk
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

flutter {
    source = "../.."
}
