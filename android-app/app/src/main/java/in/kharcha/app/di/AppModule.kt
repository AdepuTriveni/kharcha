package `in`.kharcha.app.di

import android.content.Context
import androidx.room.Room
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import `in`.kharcha.app.data.KharchaDatabase
import `in`.kharcha.app.data.OutboxDao
import `in`.kharcha.app.llm.LlamaOnDeviceParser
import `in`.kharcha.app.llm.OnDeviceParser
import java.util.concurrent.TimeUnit
import javax.inject.Singleton
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient

@Module
@InstallIn(SingletonComponent::class)
object AppModule {
    @Provides
    @Singleton
    fun database(@ApplicationContext context: Context): KharchaDatabase =
        Room.databaseBuilder(context, KharchaDatabase::class.java, "kharcha.db").build()

    @Provides
    fun outboxDao(db: KharchaDatabase): OutboxDao = db.outbox()

    @Provides
    @Singleton
    fun json(): Json = Json { ignoreUnknownKeys = true; explicitNulls = true; encodeDefaults = true }

    @Provides
    @Singleton
    fun http(): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
}

@Module
@InstallIn(SingletonComponent::class)
abstract class ParserModule {
    @Binds
    abstract fun onDeviceParser(impl: LlamaOnDeviceParser): OnDeviceParser
}
