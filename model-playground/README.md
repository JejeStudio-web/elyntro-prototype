# Elyntro Model Playground V1

A browser-based inference demo using Transformers.js and the public `Xenova/Qwen1.5-0.5B-Chat` model. The browser downloads model files and runs text generation locally; no Elyntro API key is used.

## Run

Serve this directory over HTTP (for example `python -m http.server 8080`) and open the page in a modern browser. Opening the HTML as a local file may block ES module imports. First load requires internet access and substantial browser memory.

## Limitations

This is a small pretrained third-party model, not a model trained by Elyntro and not the complete Elyntro agent. It has no tools, persistent memory, web browsing, multimodal support, or systematic evaluation. Availability depends on the public model repository and Transformers.js runtime compatibility.
