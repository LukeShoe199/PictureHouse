plugins {
    id("com.android.application")
}

// Release signing: the key lives outside the repository. build_android.py
// passes its path and password through the environment (never a file here).
val releaseKeystore = System.getenv("PICTUREHOUSE_KEYSTORE")?.let { file(it) }

android {
    namespace = "com.picturehouse.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.picturehouse.android"
        minSdk = 26 // Android 8.0
        targetSdk = 36
        // Set from PictureHouse's version by build_android.py.
        versionCode = (findProperty("phVersionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("phVersionName") as String?) ?: "0.0.0-dev"
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                val password = System.getenv("PICTUREHOUSE_KEYSTORE_PASSWORD")
                    ?: error("PICTUREHOUSE_KEYSTORE is set but PICTUREHOUSE_KEYSTORE_PASSWORD isn't")
                storeFile = releaseKeystore
                storeType = "PKCS12"
                storePassword = password
                keyAlias = "picturehouse"
                keyPassword = password
                enableV1Signing = false // minSdk 26: v2/v3 signatures only
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // The release key when given (build_android.py), else the debug key
            // (`--debug-key` test builds, or Android Studio).
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
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
