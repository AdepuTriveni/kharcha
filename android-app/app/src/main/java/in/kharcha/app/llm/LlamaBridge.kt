package `in`.kharcha.app.llm

/**
 * JNI bridge to llama.cpp (PROJECT_SPEC §15.6). The native library is only built with
 * `./gradlew -Pkharcha.llama=true -Pllama.dir=/path/to/llama.cpp assembleSideloadDebug`;
 * without it [isAvailable] is false and tier 2 is skipped (the server parses instead).
 */
object LlamaBridge {
    val isAvailable: Boolean = try {
        System.loadLibrary("kharcha_llama")
        true
    } catch (e: UnsatisfiedLinkError) {
        false
    }

    /**
     * One grammar-constrained completion on 1 thread. Returns the generated text, or null if
     * the model could not be loaded. Blocking: call from a background dispatcher.
     */
    external fun complete(
        modelPath: String,
        system: String,
        user: String,
        grammar: String,
        maxTokens: Int,
    ): String?
}
