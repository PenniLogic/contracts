// Compiles the generated Kotlin client (jvm-ktor + kotlinx.serialization) together with the
// hand-written ADR-015 seams and runs the money/instant conformance tests. Versions are exact;
// artifacts are checksum-verified through gradle/verification-metadata.xml.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.4.20"
    kotlin("plugin.serialization") version "2.4.20"
}

group = "com.pennilogic"
version = "0.0.0-smoke"

val ktorVersion = "3.6.0"
val serializationVersion = "1.11.0"
val coroutinesVersion = "1.11.0"

java {
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_21)
        allWarningsAsErrors.set(false)
    }
}

sourceSets {
    main {
        kotlin.srcDir("../../build/generated/kotlin/src/main/kotlin")
    }
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:$serializationVersion")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion")
    implementation("io.ktor:ktor-client-core:$ktorVersion")
    implementation("io.ktor:ktor-client-content-negotiation:$ktorVersion")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktorVersion")
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
    systemProperty("pennilogic.contracts.root", rootProject.projectDir.resolve("../..").canonicalPath)
    // The conformance vectors and the generated manifest are read at run time; declare them so a change re-runs the tests.
    inputs.dir(rootProject.projectDir.resolve("../../spec/fixtures"))
    inputs.file(rootProject.projectDir.resolve("../../build/generated/kotlin/contracts-manifest.json"))
    testLogging {
        events("passed", "failed", "skipped")
        showExceptions = true
        showCauses = true
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}