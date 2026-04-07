allprojects {
    extra["kotlin_version"] = "2.1.0"
    repositories {
        google()
        mavenCentral()
    }
}

subprojects {
    afterEvaluate {
        val rootDrive = rootProject.projectDir.absolutePath.substringBefore(":")
        val projectDrive = project.projectDir.absolutePath.substringBefore(":")

        if (project.hasProperty("android")) {
            val android = project.extensions.getByName("android") as com.android.build.gradle.BaseExtension
            android.compileSdkVersion(36)
        }

        // Suppress warnings-as-errors for all third-party plugins to prevent
        // deprecated API usage in speech_to_text, audioplayers, etc. from blocking build
        tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {
            kotlinOptions {
                allWarningsAsErrors = false
            }
        }

        // Disable cross-drive test tasks and incremental compilation for plugins
        // on a different drive (Windows multi-drive / pub cache on C: vs project on E:)
        if (!rootDrive.equals(projectDrive, ignoreCase = true)) {
            tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {
                kotlinOptions {
                    // Disable incremental to avoid cross-drive cache corruption
                    freeCompilerArgs = freeCompilerArgs + listOf("-Xenable-incremental-compilation=false")
                }
                outputs.upToDateWhen { false }
            }
            tasks.forEach { task ->
                if (task.name.contains("generateDebugUnitTestConfig") ||
                    task.name.contains("generateReleaseUnitTestConfig")) {
                    task.enabled = false
                }
            }
        }
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
