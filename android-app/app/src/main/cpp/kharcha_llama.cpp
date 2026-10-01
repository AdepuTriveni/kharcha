// JNI bridge: one grammar-constrained, greedy completion with llama.cpp (PROJECT_SPEC §15.6).
// Built only with -Pkharcha.llama=true -Pllama.dir=<llama.cpp checkout>. One thread, small
// context; the model stays loaded between calls, a fresh context is used per message.
#include <jni.h>

#include <mutex>
#include <string>
#include <vector>

#include "llama.h"

namespace {

std::mutex g_lock;
llama_model *g_model = nullptr;
std::string g_path;

llama_model *load(const std::string &path) {
    if (g_model != nullptr && g_path == path) return g_model;
    if (g_model != nullptr) {
        llama_model_free(g_model);
        g_model = nullptr;
    }
    llama_backend_init();
    llama_model_params params = llama_model_default_params();
    params.n_gpu_layers = 0;
    g_model = llama_model_load_from_file(path.c_str(), params);
    g_path = g_model != nullptr ? path : "";
    return g_model;
}

std::string jstr(JNIEnv *env, jstring s) {
    const char *chars = env->GetStringUTFChars(s, nullptr);
    std::string out(chars);
    env->ReleaseStringUTFChars(s, chars);
    return out;
}

std::string run(llama_model *model, const std::string &system, const std::string &user,
                const std::string &grammar, int max_tokens) {
    const llama_vocab *vocab = llama_model_get_vocab(model);

    llama_chat_message messages[2] = {{"system", system.c_str()}, {"user", user.c_str()}};
    const char *tmpl = llama_model_chat_template(model, nullptr);
    std::vector<char> buf(system.size() + user.size() + 512);
    int n = llama_chat_apply_template(tmpl, messages, 2, true, buf.data(), (int) buf.size());
    if (n > (int) buf.size()) {
        buf.resize(n);
        n = llama_chat_apply_template(tmpl, messages, 2, true, buf.data(), (int) buf.size());
    }
    if (n < 0) return "";
    const std::string prompt(buf.data(), n);

    const int n_prompt = -llama_tokenize(vocab, prompt.c_str(), (int) prompt.size(), nullptr, 0, true, true);
    std::vector<llama_token> tokens(n_prompt);
    if (llama_tokenize(vocab, prompt.c_str(), (int) prompt.size(), tokens.data(), n_prompt, true, true) < 0) {
        return "";
    }

    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = n_prompt + max_tokens + 8;
    cparams.n_batch = n_prompt;
    cparams.n_threads = 1;
    cparams.n_threads_batch = 1;
    llama_context *ctx = llama_init_from_model(model, cparams);
    if (ctx == nullptr) return "";

    llama_sampler *sampler = llama_sampler_chain_init(llama_sampler_chain_default_params());
    llama_sampler_chain_add(sampler, llama_sampler_init_grammar(vocab, grammar.c_str(), "root"));
    llama_sampler_chain_add(sampler, llama_sampler_init_greedy());

    std::string out;
    llama_batch batch = llama_batch_get_one(tokens.data(), (int) tokens.size());
    for (int i = 0; i < max_tokens; ++i) {
        if (llama_decode(ctx, batch) != 0) break;
        llama_token id = llama_sampler_sample(sampler, ctx, -1);
        if (llama_vocab_is_eog(vocab, id)) break;
        char piece[256];
        const int len = llama_token_to_piece(vocab, id, piece, sizeof(piece), 0, true);
        if (len > 0) out.append(piece, len);
        batch = llama_batch_get_one(&id, 1);
    }
    llama_sampler_free(sampler);
    llama_free(ctx);
    return out;
}

}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_in_kharcha_app_llm_LlamaBridge_complete(JNIEnv *env, jobject /* this */, jstring model_path,
                                             jstring system, jstring user, jstring grammar,
                                             jint max_tokens) {
    std::lock_guard<std::mutex> guard(g_lock);
    llama_model *model = load(jstr(env, model_path));
    if (model == nullptr) return nullptr;
    const std::string text =
        run(model, jstr(env, system), jstr(env, user), jstr(env, grammar), (int) max_tokens);
    return env->NewStringUTF(text.c_str());
}
