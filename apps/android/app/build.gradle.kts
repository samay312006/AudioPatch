plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.audiopatch"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.audiopatch"
        minSdk = 29          // Android 10: first version with AudioPlaybackCapture
        targetSdk = 35
        versionCode = 1
        versionName = "0.3.0"
    }

    // Release signing comes from ~/.gradle/gradle.properties (never from the repo):
    // AUDIOPATCH_KEYSTORE, AUDIOPATCH_KEYSTORE_PASSWORD, AUDIOPATCH_KEY_ALIAS, AUDIOPATCH_KEY_PASSWORD.
    // Without them, release builds are unsigned and debug builds work as usual.
    val keystore = providers.gradleProperty("AUDIOPATCH_KEYSTORE").orNull
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = providers.gradleProperty("AUDIOPATCH_KEYSTORE_PASSWORD").get()
                keyAlias = providers.gradleProperty("AUDIOPATCH_KEY_ALIAS").get()
                keyPassword = providers.gradleProperty("AUDIOPATCH_KEY_PASSWORD").get()
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")  // real org.json for JVM tests (Android's is a stub there)
}
