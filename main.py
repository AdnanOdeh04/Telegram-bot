# Import Google Gemini AI client and types
from google import genai
from google.genai import types

# Standard library for WAV processing
import wave

# Telegram bot imports
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    filters,
    MessageHandler,
)

# Local API keys
from api_keys import Telegram_api_key, Gemini_api_key

# LangChain LLM (Ollama) and memory
from langchain.memory import ConversationBufferMemory, VectorStoreRetrieverMemory
from langchain.chains import ConversationChain

# IO utilities
from io import BytesIO

# LangChain integration with Google Generative AI (Gemini)
from langchain_google_genai import ChatGoogleGenerativeAI

# Embeddings and vector store for memory persistence
from langchain.embeddings import HuggingFaceInstructEmbeddings
from langchain_community.vectorstores import Chroma

# Initialize Gemini client for TTS/STT
gemini_client = genai.Client(api_key=Gemini_api_key)


def pcm_to_wav_bytes(pcm_bytes, channels=1, rate=24000, width=2):
    """
    Convert raw PCM bytes to a WAV file buffer.
    - pcm_bytes: raw audio data
    - channels: number of audio channels
    - rate: sampling rate (Hz)
    - width: bytes per sample
    Returns an in-memory BytesIO buffer containing WAV data.
    """
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)
    buf.name = "speech.wav"
    buf.seek(0)
    return buf


# --------------------
# Set up persistent vector memory
# --------------------
persist_directory = "db"

# 1. Initialize embedding model (Instructor) on CPU
embeddings = HuggingFaceInstructEmbeddings(
    model_name="hkunlp/instructor-xl",  # choose desired Instructor model
    model_kwargs={"device": "cpu"},
)

# 2. Create (or load) Chroma vector store for memory persistence
vector_store = Chroma(
    persist_directory=persist_directory,
    embedding_function=embeddings,
)

# --------------------
# Instantiate your LLMs
# --------------------


# Use Google Gemini via LangChain
LLM = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",   # choose your Gemini model
    temperature=0.0,
    google_api_key=Gemini_api_key,
)

# --------------------
# Configure memory to use vector-store retrieval
# --------------------
memory = VectorStoreRetrieverMemory(
    vectorstore=vector_store,
    memory_key="history",                # prompt variable name
    return_messages=False,                 # we retrieve raw text
    retriever=vector_store.as_retriever(
        search_kwargs={"k": 5}            # fetch top 5 similar contexts
    ),
)

# Build conversation chain with LLM + memory
conversation = ConversationChain(
    llm=LLM,
    memory=memory,
    verbose=True,
)

# Telegram API key
telegram_api_key = Telegram_api_key

# --------------------
# Telegram command handlers
# --------------------
async def start(update: Update, context):
    """Handle /start command"""
    await update.message.reply_text("Hello! This is Adnan's Bot.")

async def AnyCommand(update: Update, context):
    """Handle general text messages"""
    user_text = update.message.text

    # Optional: acknowledge receipt
    await update.message.reply_text("Processing your request…")

    # Generate response using conversation chain
    bot_text = conversation.predict(input=user_text)

    # Send bot response
    await update.message.reply_text(bot_text)

    # Persist this turn to memory
    memory.save_context({"input": user_text}, {"output": bot_text})
    vector_store.persist()

async def gen_audio(update: Update, context):
    """Generate speech audio from text following /gen_audio"""
    # Extract text after the command
    text = update.message.text.partition(" ")[2].strip()

    # Call Gemini TTS model
    resp = gemini_client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Kore"
                    )
                )
            ),
        ),
    )

    # Extract PCM bytes and convert to WAV
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    wav_buf = pcm_to_wav_bytes(pcm)

    # Reply with audio file
    await update.message.reply_audio(audio=wav_buf)

async def receive_voice(update: Update, context):
    """Receive voice message, transcribe with Gemini, and reply"""
    voice = update.message.voice
    file_obj = await context.bot.get_file(voice.file_id)

    # Download OGG/OPUS audio into memory
    audio_buffer = BytesIO()
    await file_obj.download_to_memory(out=audio_buffer)
    audio_bytes = audio_buffer.getvalue()

    # Call Gemini STT model
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            "Please provide a verbatim transcript of this audio.",
            types.Part.from_bytes(
                data=audio_bytes,
                mime_type="audio/ogg; codecs=opus"
            )
        ],
    )

    # Extract transcript
    transcript = getattr(response, "text", None) or response.candidates[0].content.text

    # Reply with the transcription
    await update.message.reply_text(transcript)

async def gen_text(update: Update, context):
    """Enable voice handler when /gen_text is called"""
    app.add_handler(MessageHandler(filters.VOICE, receive_voice))

# --------------------
# Bot setup and polling
# --------------------
app = ApplicationBuilder().token(telegram_api_key).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("gen_audio", gen_audio))
app.add_handler(CommandHandler("gen_text", gen_text))
app.add_handler(MessageHandler(filters.TEXT, AnyCommand))

# Start the bot
app.run_polling()
