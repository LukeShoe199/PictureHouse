plugins {
    id("com.android.application")
}

android {
    namespace = "com.picturehouse.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.picturehouse.android"
        minSdk = 26 // Android 8.0
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Personal sideloaded app: signed with this PC's Android debug key so
            // updates install over the top. Use a real keystore before sharing it.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}
