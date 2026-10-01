package `in`.kharcha.app.data

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Test

/** The backend checks the same fixture against `kharcha_common.categories.Category`. */
class CategoryContractTest {
    @Test
    fun categoriesMatchBackendFixture() {
        val text = javaClass.classLoader!!.getResource("contract/categories.json")!!.readText()
        assertEquals(Json.decodeFromString<List<String>>(text), Category.entries.map { it.name })
    }
}
