package `in`.kharcha.app.llm

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import `in`.kharcha.app.sync.DeviceParseDto
import `in`.kharcha.app.sync.DeviceParseResultDto
import `in`.kharcha.app.util.Money
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Tier 2 (PROJECT_SPEC §10.1): parse a redacted message on the phone. */
interface OnDeviceParser {
    suspend fun parse(sender: String?, sourceApp: String?, text: String): DeviceParseDto?
}

/** The model's raw output: the canonical training target (see assets/parser.gbnf). */
@Serializable
data class ModelOutput(
    val isTransaction: Boolean,
    val amount: String? = null,
    val direction: String? = null,
    val channel: String? = null,
    val status: String? = null,
    val merchantRaw: String? = null,
    val counterpartyVpa: String? = null,
    val referenceId: String? = null,
    val accountHint: String? = null,
    val balanceAfter: String? = null,
    val promisedRefundDays: Int? = null,
)

/** Prompt sections from assets/parser_prompt.md (a copy of backend/prompts/parser/v1.md). */
data class PromptSections(val system: String, val user: String) {
    fun userFor(sender: String?, sourceApp: String?, text: String): String =
        user.replace("{sender}", sender ?: "unknown")
            .replace("{source_app}", sourceApp ?: "unknown")
            .replace("{text}", text.replace(">>>", "> > >"))

    companion object {
        fun parse(markdown: String): PromptSections {
            val body = if (markdown.startsWith("---")) markdown.split("---", limit = 3)[2] else markdown
            val sections = Regex("^## (\\w+)\\s*$", RegexOption.MULTILINE).split(body)
            val names = Regex("^## (\\w+)\\s*$", RegexOption.MULTILINE).findAll(body).map { it.groupValues[1] }.toList()
            val map = names.zip(sections.drop(1).map { it.trim() }).toMap()
            return PromptSections(map.getValue("system"), map.getValue("user"))
        }
    }
}

private val outputJson = Json { ignoreUnknownKeys = true }

/** Model text -> wire DTO; null when it is not a transaction or cannot be used. */
fun toDeviceParse(raw: String, modelVersion: String, latencyMs: Long): DeviceParseDto? {
    val out = try {
        outputJson.decodeFromString(ModelOutput.serializer(), raw.trim())
    } catch (e: kotlinx.serialization.SerializationException) {
        return null
    } catch (e: IllegalArgumentException) {
        return null
    }
    if (!out.isTransaction) return null
    val paise = out.amount?.let(Money::parseRupees) ?: return null
    return DeviceParseDto(
        modelVersion = modelVersion,
        result = DeviceParseResultDto(
            amountPaise = paise,
            direction = out.direction ?: return null,
            channel = out.channel ?: "UNKNOWN",
            status = out.status ?: "SUCCESS",
            merchantRaw = out.merchantRaw,
            referenceId = out.referenceId,
        ),
        latencyMs = latencyMs,
    )
}

/** Runs the installed GGUF through llama.cpp with the JSON grammar; 5 s budget per message. */
@Singleton
class LlamaOnDeviceParser @Inject constructor(
    @ApplicationContext private val context: Context,
    private val models: ModelManager,
) : OnDeviceParser {
    private val prompt by lazy {
        PromptSections.parse(context.assets.open("parser_prompt.md").bufferedReader().readText())
    }
    private val grammar by lazy { context.assets.open("parser.gbnf").bufferedReader().readText() }

    override suspend fun parse(sender: String?, sourceApp: String?, text: String): DeviceParseDto? {
        if (!LlamaBridge.isAvailable) return null
        val model = models.installed() ?: return null
        val start = System.nanoTime()
        val raw = withTimeoutOrNull(TIMEOUT_MS) {
            runInterruptible(Dispatchers.Default) {
                LlamaBridge.complete(model.path, prompt.system, prompt.userFor(sender, sourceApp, text), grammar, MAX_TOKENS)
            }
        } ?: return null
        return toDeviceParse(raw, model.version, (System.nanoTime() - start) / 1_000_000)
    }

    private companion object {
        const val TIMEOUT_MS = 5_000L
        const val MAX_TOKENS = 160
    }
}
