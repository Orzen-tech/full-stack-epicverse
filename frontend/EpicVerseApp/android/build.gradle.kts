plugins {

    id("com.android.application") version "8.13.2" apply false

    id("org.jetbrains.kotlin.android") version "2.1.0" apply false

}



allprojects {

    extra["kotlin_version"] = "2.1.0"

    repositories {

        google()

        mavenCentral()

    }

}



// Redirect Gradle build outputs from android/app/build to <flutter-root>/build/app

// so the Flutter tool can locate the produced APK / AAB. Without this, every

// Flutter build emits "Gradle build failed to produce an .apk file" even though

// the APK is sitting under android/app/build/outputs/apk/.

val newBuildDir: Directory = rootProject.layout.buildDirectory.dir("../../build").get()

rootProject.layout.buildDirectory.value(newBuildDir)



subprojects {

    // Only redirect build dir for subprojects on the same drive as the root.
    // Pub-cache plugins live on C:\ while the project is on E:\; redirecting
    // their build dir causes a "different roots" Kotlin compiler crash.
    val rootDriveSub = rootProject.projectDir.absolutePath.substringBefore(":").uppercase()
    val projectDriveSub = project.projectDir.absolutePath.substringBefore(":").uppercase()

    if (rootDriveSub == projectDriveSub) {

        val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)

        project.layout.buildDirectory.value(newSubprojectBuildDir)

    }

}



subprojects {

    afterEvaluate {

        val rootDrive = rootProject.projectDir.absolutePath.substringBefore(":")

        val projectDrive = project.projectDir.absolutePath.substringBefore(":")



        if (project.extensions.findByName("android") != null) {

            val android = project.extensions.getByName("android") as com.android.build.gradle.BaseExtension

            

            android.compileSdkVersion(36)

            

            // Force Java compatibility to 17 for all subprojects

            android.compileOptions {

                sourceCompatibility = JavaVersion.VERSION_17

                targetCompatibility = JavaVersion.VERSION_17

            }

            

            // Handle Kotlin DSL specifically for android block if it's there

            if (android is com.android.build.gradle.AppExtension || android is com.android.build.gradle.LibraryExtension) {

                // Ensure the kotlinOptions inside the android block also match

                val kotlinOptions = (android as? org.gradle.api.plugins.ExtensionAware)?.extensions?.findByName("kotlinOptions") as? org.jetbrains.kotlin.gradle.dsl.KotlinJvmOptions

                kotlinOptions?.jvmTarget = "17"

            }

        }



        // Force Kotlin compiler tasks to use JVM 17

        tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {

            compilerOptions {

                allWarningsAsErrors.set(false)

                jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)

                // Fix for Inconsistent JVM-target: explicitly set apiVersion and languageVersion

                apiVersion.set(org.jetbrains.kotlin.gradle.dsl.KotlinVersion.KOTLIN_2_1)

                languageVersion.set(org.jetbrains.kotlin.gradle.dsl.KotlinVersion.KOTLIN_2_1)

            }

        }

        

        // Force JavaCompile tasks to use JVM 17

        tasks.withType<JavaCompile>().configureEach {

            sourceCompatibility = "17"

            targetCompatibility = "17"

        }



        if (!rootDrive.equals(projectDrive, ignoreCase = true)) {

            tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {

                compilerOptions {

                    freeCompilerArgs.add("-Xno-incremental-compilation")

                }

                outputs.upToDateWhen { false }

            }

            tasks.matching { it.name.contains("generateDebugUnitTestConfig") || it.name.contains("generateReleaseUnitTestConfig") }

                 .configureEach { enabled = false }

        }

    }

}



tasks.register<Delete>("clean") {

    delete(rootProject.layout.buildDirectory)

}

