---
layout: default
title: Dolphin Grok Hybrid
description: Local Dolphin-Mistral 7B chat, optional Grok routes, and an OpenAI-compatible API.
samwiki: true
---

<section class="sw-lede" aria-labelledby="what-title">
  <div class="sw-lede-copy">
    <h2 id="what-title">A local 7B chat server with a Grok side door</h2>
    <p>dolphin_grok_hybrid is a FastAPI application that keeps ordinary chat on a local <strong>Dolphin-Mistral 7B</strong> model through Ollama, and calls <strong>xAI Grok</strong> for image and video. The README documents an OpenAI-shaped <code>/v1/chat/completions</code> route, streaming and one-shot replies, system prompts rated G, PG, R, and NC17, and mathematics written as LaTeX. Supabase is the documented store for chat logs, speech metadata, and SAAM notes, which the README describes as STAR-method entries for medical and stress situations. The copy of <code>main.py</code> in this repository is a short status app; the feature list and run steps live in <code>README.md</code> and <code>NEW_BLUEPRINTS.md</code>. The weights stay in Ollama.</p>
    <p>Two educational pages already sit in <code>static/hub/</code>: an overview of the <a href="{{ '/static/hub/second-amendment.html' | relative_url }}">Second Amendment</a>, and notes on a Supreme Court sports case with a saved chat log, <a href="{{ '/static/hub/trans-athletes-sports.html' | relative_url }}">trans athletes in sports</a>. Those files keep their own markup. This page is the GitHub Pages front door.</p>
  </div>
  <aside class="sw-find" aria-labelledby="find-title">
    <h2 id="find-title">What you run</h2>
    <ul>
      <li><strong>Local model</strong> <code>dolphin-mistral:7b</code> via Ollama.</li>
      <li><strong>Chat API</strong> <code>POST /v1/chat/completions</code>, streaming or one-shot.</li>
      <li><strong>Control</strong> A G / PG / R / NC17 system prompt.</li>
      <li><strong>Side routes</strong> Documented <code>/tts</code>, <code>/imagine</code>, and <code>/saam</code>.</li>
      <li><strong>Remote piece</strong> xAI when the request is Grok image or video.</li>
    </ul>
  </aside>
</section>

## How a reply is produced

Dolphin-Mistral 7B is an instruction fine-tune of Mistral 7B, a decoder-only transformer. The base model was fit with next-token cross-entropy. For a token sequence {::nomarkdown}\(x_1,\ldots,x_T\){:/} and parameters {::nomarkdown}\(\theta\){:/}, the loss is the negative log probability of each token given the tokens before it. The probability of a whole reply is the product of those one-step conditionals. At chat time the server samples from the distribution the checkpoint already learned. Dolphin’s fine-tune changes how that distribution follows instructions. The attention layout underneath is still Mistral’s.

{::nomarkdown}
<div>
\[
\mathcal{L}(\theta) = -\sum_{t=1}^{T} \log p_\theta(x_t \mid x_{<t}),
\qquad
p(x_{1:T}) = \prod_{t=1}^{T} p_\theta(x_t \mid x_{<t}).
\]
</div>
{:/}

Mistral 7B publishes two concrete ways to keep that product affordable. Grouped-query attention uses 32 query heads and 8 key/value heads, so each key/value head is shared by four query heads and the KV cache is about a quarter of full multi-head attention. Sliding-window attention limits each position to a recent window of 4096 tokens, so the attention work in a layer grows with the window length. The model dimension is 4096 across 32 layers. Those figures are the published base architecture this Dolphin checkpoint starts from.

The hybrid is a router. Text, including the rating prefix and any TeX in the reply, stays on the local conditional model. Image and video are the documented Grok routes, because those requests ask for pixels and frames. The G / PG / R / NC17 switch is a choice among four system prompts, a discrete prefix in the context. When a reply contains mathematics, the model emits TeX in the token stream, and a client that understands `$...$` or `\(...\)` draws it. Speech sits beside that language-model loss: the README names Qwen3 for text-to-speech, `NEW_BLUEPRINTS.md` names Piper ONNX, and the audio metadata is stored in Supabase. Image generation is procedural Minecraft-style pixel art on CPU. The video note describes a prompt builder that turns a chat reply, sometimes together with one of those images, into a Grok video request.

## Using it

1. Clone the repository and install `requirements.txt` (`fastapi`, `uvicorn`, `httpx`, `python-dotenv`, `pydantic`, `supabase`).
2. Start Ollama on `dolphin-mistral:7b`.
3. Put secrets in an untracked `.env`: `OLLAMA_BASE_URL`, Supabase credentials, and an xAI key if you call the Grok routes.
4. Launch `uvicorn main:app --host 0.0.0.0 --port 8000`. The README points at `scripts/start_app.sh` for a startup example.
5. Send chat as an OpenAI-style `messages` array to `/v1/chat/completions`. The README also lists `/tts`, `/imagine`, `/saam`, a SOAP route, visitor logging, and a trending-news fetch.

- **Loss.** Next-token cross-entropy on the published base model. Chat time reads that distribution.
- **Attention.** Grouped-query attention (32 query heads, 8 key/value heads) and a 4096-token sliding window, as published for Mistral 7B.
- **Decoding.** A reply is a product of conditional token probabilities from the local checkpoint.
- **Control.** Rating labels select a system prompt, which is extra context in front of the user text.
- **Routing.** Local Ollama for text. xAI for the documented image and video routes.
- **Math in a reply.** LaTeX is text the model generated. Rendering is a client step.

The source for this site is the [dolphin_grok_hybrid](https://github.com/sdcastillo/dolphin_grok_hybrid) repository. Hub links: [Home](https://sdcastillo.github.io/), [About](https://sdcastillo.github.io/about/), and [Code](https://sdcastillo.github.io/code/).

<script>
  window.MathJax = {
    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']] },
    options: { skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }
  };
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" id="MathJax-script" async></script>
