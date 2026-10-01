package `in`.kharcha.app.sync

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class SettingsApiTest {
    private lateinit var server: MockWebServer
    private val api = ApiClient(OkHttpClient(), Json { ignoreUnknownKeys = true; explicitNulls = true })
    private val body = """{"userId":"u_1","displayName":null,"roastLevel":"MEDIUM","quietStart":"22:00:00",
        "quietEnd":"08:00:00","mlConsent":true,"experimentOptIn":false,"telegramLinked":true,"budgets":[]}"""

    @Before
    fun setUp() {
        server = MockWebServer().also { it.start() }
    }

    @After
    fun tearDown() = server.shutdown()

    private fun url() = server.url("/").toString().trimEnd('/')

    @Test
    fun updateSendsOnlyChangedFields() = runTest {
        server.enqueue(MockResponse().setBody(body))
        val result = api.updateUserSettings(url(), "k", UserSettingsUpdateDto(mlConsent = true))
        assertTrue((result as ApiResult.Ok).value.mlConsent)
        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/v1/settings", request.path)
        assertEquals("""{"mlConsent":true}""", request.body.readUtf8())
    }

    @Test
    fun deleteMeCallsDelete() = runTest {
        server.enqueue(MockResponse().setResponseCode(204))
        assertTrue(api.deleteMe(url(), "k") is ApiResult.Ok)
        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/v1/me", request.path)
    }
}
