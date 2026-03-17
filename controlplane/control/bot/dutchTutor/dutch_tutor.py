from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import deque
from typing import Any

from controlplane.boundary.llminterface.chatgpt_interface import ChatGPTInterface
from communicationPlane.whatsappEngine.whapiInterface.whapi_client import WhapiClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)
VOCAB_BANK_PATH = os.path.join(_DIR, "vocab_bank.json")
QUIZ_BANK_PATH = os.path.join(_DIR, "quiz_bank.json")

GENERATE_KNM_VOCAB_PROMPT = (
    "Generate a JSON array of 200 essential Dutch words and verbs for KNM "
    "(Kennis van de Nederlandse Maatschappij) and general inburgering preparation. "
    "Focus on the MOST IMPORTANT everyday Dutch words, verbs, and civic terms "
    "that a new immigrant absolutely needs to know.\n\n"

    "WORD TYPES TO INCLUDE (in this priority):\n"
    "1. VERBS (at least 50%): Essential Dutch verbs used in daily life and KNM contexts — "
    "bellen, gaan, komen, werken, wonen, betalen, aanvragen, inschrijven, stemmen, kiezen, "
    "leren, studeren, solliciteren, verhuizen, huren, kopen, maken, hebben, zijn, worden, "
    "moeten, mogen, kunnen, willen, zullen, doen, zien, geven, krijgen, zoeken, vinden, "
    "beginnen, stoppen, helpen, vragen, antwoorden, schrijven, lezen, spreken, luisteren, "
    "eten, drinken, slapen, rijden, fietsen, lopen, wachten, begrijpen, kennen, weten, "
    "voelen, denken, vergeten, onthouden, opbellen, afspreken, invullen, ondertekenen, "
    "verzekeren, regelen, melden, klagen\n"
    "2. NOUNS: Key KNM nouns — huisarts, gemeente, school, werk, belasting, uitkering, "
    "zorgverzekering, paspoort, rijbewijs, DigiD, huur, contract, loon, boete, afspraak, "
    "formulier, vergunning, wijk, buur, politie, brandweer, ambulance\n"
    "3. GENERAL DUTCH: Daily words — dag, week, maand, geld, huis, straat, winkel, "
    "trein, bus, fiets, auto, brood, water, dokter, ziek, gezond, duur, goedkoop, "
    "open, dicht, links, rechts, hier, daar, vandaag, morgen, gisteren\n"
    "4. KNM CIVIC TERMS: provincie, Tweede Kamer, Grondwet, koning, minister-president, "
    "verkiezingen, vrijheid, gelijkheid, discriminatie, leerplicht, kinderbijslag, "
    "toeslagen, Belastingdienst, UWV\n\n"

    "Each entry must be a JSON object with these keys:\n"
    "1. \"dutch\": the Dutch word or verb (infinitive for verbs)\n"
    "2. \"english\": English meaning\n"
    "3. \"type\": one of [\"verb\", \"noun\", \"adjective\", \"phrase\", \"fact\"]\n"
    "4. \"example_dutch\": a short A2-level Dutch sentence using the word\n"
    "5. \"example_english\": English translation of the sentence\n"
    "6. \"category\": one of [\"healthcare\", \"children\", \"tax\", \"government\", "
    "\"society\", \"unemployment\", \"education\", \"country_knowledge\", \"daily_life\"]\n"
    "7. \"difficulty\": one of [\"easy\", \"medium\"]\n\n"

    "Requirements:\n"
    "- At least 50% of entries MUST be verbs\n"
    "- Use simple A2 Dutch\n"
    "- Focus on words you will actually hear in a gemeente, hospital, school, or workplace\n"
    "- Include common separable verbs (e.g. opbellen, meenemen, aanvragen)\n"
    "- Include modal verbs (moeten, mogen, kunnen, willen)\n"
    "- No rare, literary, or advanced words\n"
    "- Make examples realistic daily-life or KNM situations\n\n"

    "Return ONLY valid JSON. No markdown. No explanation.\n\n"

    "Example:\n"
    "[\n"
    "  {\n"
    "    \"dutch\": \"aanvragen\",\n"
    "    \"english\": \"to apply for\",\n"
    "    \"type\": \"verb\",\n"
    "    \"example_dutch\": \"Ik moet een paspoort aanvragen bij de gemeente.\",\n"
    "    \"example_english\": \"I have to apply for a passport at the municipality.\",\n"
    "    \"category\": \"government\",\n"
    "    \"difficulty\": \"easy\"\n"
    "  }\n"
    "]"
)

GENERATE_KNM_QUESTION_BANK_PROMPT = (
    "Generate a JSON array of 250 KNM-style multiple-choice questions for the Dutch inburgering KNM exam. "
    "The bank must combine:\n"
    "1. KNM social knowledge\n"
    "2. DUO-style practical situations\n"
    "3. Dutch-to-English translation support\n"
    "4. Basic Netherlands civic and country knowledge\n"

    "Cover these topics:\n"
    "- Healthcare\n"
    "- Children and family\n"
    "- Tax and benefits\n"
    "- Government and municipality\n"
    "- Society and norms\n"
    "- Work and unemployment benefits\n"
    "- Education\n"
    "- Country knowledge about the Netherlands\n"

    "Include frequent KNM themes such as:\n"
    "- huisarts, apotheek, ziekenhuis, zorgverzekering\n"
    "- schoolplicht / leerplicht, kinderopvang\n"
    "- belasting, toeslagen, Belastingdienst\n"
    "- gemeente, provincie, DigiD, verkiezingen, politie\n"
    "- vrijheid, gelijkheid, discriminatie, respect\n"
    "- UWV, werkloos, uitkering, contract, werkgever\n"
    "- basisschool, middelbare school, mbo, universiteit\n"
    "- number of provinces, names of provinces, capitals of provinces, Amsterdam as capital, the King, the Prime Minister, parliament, emergency number 112\n"

    "Important civic fact handling:\n"
    "- The Netherlands has 12 provinces\n"
    "- Amsterdam is the capital of the Netherlands\n"
    "- The King is the head of state\n"
    "- The Prime Minister is the head of government\n"
    "- Questions may test province-capital recognition\n"
    "- Focus on stable civic knowledge and widely taught integration facts\n"
    "- Avoid detailed or unstable news trivia\n"

    "Each question must be a JSON object with these keys:\n"
    "1. \"id\": integer starting from 1\n"
    "2. \"question_dutch\": the question in simple Dutch\n"
    "3. \"question_english\": English translation\n"
    "4. \"options\": array of 3 objects, each with:\n"
    "   - \"label\": \"A\", \"B\", or \"C\"\n"
    "   - \"dutch\": option in Dutch\n"
    "   - \"english\": English translation\n"
    "5. \"correct_answer\": one of [\"A\", \"B\", \"C\"]\n"
    "6. \"explanation\": short English explanation\n"
    "7. \"category\": one of [\"healthcare\", \"children\", \"tax\", \"government\", \"society\", \"unemployment\", \"education\", \"country_knowledge\"]\n"
    "8. \"difficulty\": one of [\"easy\", \"medium\", \"tricky\"]\n"
    "9. \"pattern\": one of [\"situation\", \"fact\", \"definition\", \"translation\", \"civic_knowledge\"]\n"

    "Requirements:\n"
    "- Use simple A2 Dutch\n"
    "- Make the style similar to DUO and KNM practice exams\n"
    "- Most questions should be practical and situation-based\n"
    "- Include at least 40 questions on general Netherlands civic knowledge\n"
    "- Include at least 25 questions involving Dutch-to-English understanding\n"
    "- Include at least 30 'tricky' questions that test common confusion, such as huisarts vs ziekenhuis, age 4 vs age 5, gemeente vs provincie, King vs Prime Minister\n"
    "- Use realistic distractors\n"
    "- Avoid duplicate questions\n"
    "- Make the JSON valid\n"

    "Return ONLY valid JSON. No markdown. No explanations outside JSON.\n"

    "Example:\n"
    "[\n"
    "  {\n"
    "    \"id\": 1,\n"
    "    \"question_dutch\": \"U bent ziek. Waar gaat u eerst naartoe?\",\n"
    "    \"question_english\": \"You are sick. Where do you go first?\",\n"
    "    \"options\": [\n"
    "      {\"label\": \"A\", \"dutch\": \"de huisarts\", \"english\": \"the GP\"},\n"
    "      {\"label\": \"B\", \"dutch\": \"het ziekenhuis\", \"english\": \"the hospital\"},\n"
    "      {\"label\": \"C\", \"dutch\": \"de apotheek\", \"english\": \"the pharmacy\"}\n"
    "    ],\n"
    "    \"correct_answer\": \"A\",\n"
    "    \"explanation\": \"In the Netherlands, you usually go to the GP first.\",\n"
    "    \"category\": \"healthcare\",\n"
    "    \"difficulty\": \"easy\",\n"
    "    \"pattern\": \"situation\"\n"
    "  }\n"
    "]"
)

UPDATE_BANK_PROMPT = (
    "You are a Dutch KNM tutor assistant. The user wants to update a question bank. "
    "Bank type: {bank_type}\n\n"
    "Here is their instruction:\n\n{instruction}\n\n"
    "Here is the current bank:\n{current_bank}\n\n"
    "Apply the user's instruction to modify the bank. "
    "Return ONLY the complete updated JSON array (same format as input), no extra text."
)

ONE_HOUR_SECONDS = 60 * 60
MESSAGES_PER_HOUR = 5
VOCAB_WEIGHT = 0.75
ITEMS_PER_MESSAGE = 3
REVISION_DELAY_HOURS = 3
REVISION_CHANCE = 0.35

KNM_PRIORITY_CATEGORIES = {
    "healthcare", "government", "children", "country_knowledge",
}
KNM_PRIORITY_DIFFICULTIES = {"tricky", "medium"}

QUIZ_BATCH_CATEGORIES = [
    ("healthcare", 35),
    ("children", 30),
    ("tax", 30),
    ("government", 35),
    ("society", 30),
    ("unemployment", 30),
    ("education", 30),
    ("country_knowledge", 30),
]

QUIZ_BATCH_PROMPT_TEMPLATE = (
    "Generate a JSON array of exactly {count} KNM-style multiple-choice questions "
    "for the Dutch inburgering KNM exam, focused on the category: {category}.\n\n"
    + GENERATE_KNM_QUESTION_BANK_PROMPT.split("Cover these topics:")[0]
    + "Each question must be a JSON object with these keys:\n"
    "1. \"id\": integer starting from {start_id}\n"
    "2. \"question_dutch\": the question in simple Dutch\n"
    "3. \"question_english\": English translation\n"
    "4. \"options\": array of 3 objects, each with:\n"
    "   - \"label\": \"A\", \"B\", or \"C\"\n"
    "   - \"dutch\": option in Dutch\n"
    "   - \"english\": English translation\n"
    "5. \"correct_answer\": one of [\"A\", \"B\", \"C\"]\n"
    "6. \"explanation\": short English explanation\n"
    "7. \"category\": \"{category}\"\n"
    "8. \"difficulty\": one of [\"easy\", \"medium\", \"tricky\"]\n"
    "9. \"pattern\": one of [\"situation\", \"fact\", \"definition\", \"translation\", \"civic_knowledge\"]\n\n"
    "Use simple A2 Dutch. Include tricky distractors. Avoid duplicates. "
    "Return ONLY valid JSON. No markdown. No extra text."
)


class DutchTutor:
    def __init__(
        self,
        chat_id: str | None = None,
        whapi_client: WhapiClient | None = None,
        llm: ChatGPTInterface | None = None,
    ) -> None:
        self._chat_id = chat_id or os.getenv("DUTCH_TUTOR_CHAT_ID", "")
        self._whapi = whapi_client or WhapiClient()
        self._llm = llm or ChatGPTInterface()
        self._vocab_bank: list[dict[str, Any]] = []
        self._quiz_bank: list[dict[str, Any]] = []
        self._sent_vocab_indices: set[int] = set()
        self._sent_quiz_indices: set[int] = set()
        self._vocab_history: deque[tuple[int, float]] = deque(maxlen=200)
        self._quiz_history: deque[tuple[int, float]] = deque(maxlen=200)
        self._timer_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._banks_ready = False
        self._load_or_generate_banks()

    # ── Bank persistence helpers ─────────────────────────────────────

    @staticmethod
    def _load_json(path: str) -> list[dict[str, Any]] | None:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s: %s", path, exc)
        return None

    @staticmethod
    def _save_json(path: str, data: list[dict[str, Any]]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Failed to save %s: %s", path, exc)

    @staticmethod
    def _clean_llm_json(raw: str) -> str:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return cleaned

    def _generate_from_prompt(self, prompt: str) -> list[dict[str, Any]]:
        raw = self._llm.generate(prompt)
        cleaned = self._clean_llm_json(raw)
        bank = json.loads(cleaned)
        if isinstance(bank, list) and len(bank) > 0:
            return bank
        raise ValueError("LLM returned empty or non-list")

    # ── Bank loading / generation ────────────────────────────────────

    def _load_or_generate_banks(self) -> None:
        loaded_vocab = self._load_json(VOCAB_BANK_PATH)
        if loaded_vocab:
            self._vocab_bank = loaded_vocab
            logger.info("Loaded %d vocab items", len(self._vocab_bank))

        loaded_quiz = self._load_json(QUIZ_BANK_PATH)
        if loaded_quiz:
            self._quiz_bank = loaded_quiz
            logger.info("Loaded %d quiz questions", len(self._quiz_bank))

        needs_vocab = not self._vocab_bank
        needs_quiz = not self._quiz_bank

        if needs_vocab or needs_quiz:
            logger.info("Starting background bank generation (vocab=%s, quiz=%s)", needs_vocab, needs_quiz)
            thread = threading.Thread(
                target=self._background_generate, args=(needs_vocab, needs_quiz), daemon=True
            )
            thread.start()
        else:
            self._banks_ready = True

    def _background_generate(self, gen_vocab: bool, gen_quiz: bool) -> None:
        if gen_vocab:
            self._generate_vocab_bank()
        if gen_quiz:
            self._generate_quiz_bank()
        self._banks_ready = True
        logger.info("Background bank generation complete (vocab=%d, quiz=%d)",
                    len(self._vocab_bank), len(self._quiz_bank))

    def _generate_vocab_bank(self) -> None:
        logger.info("Generating KNM vocab bank via ChatGPT...")
        try:
            self._vocab_bank = self._generate_from_prompt(GENERATE_KNM_VOCAB_PROMPT)
            self._save_json(VOCAB_BANK_PATH, self._vocab_bank)
            logger.info("Generated %d vocab items", len(self._vocab_bank))
        except Exception as exc:
            logger.error("Failed to generate vocab bank: %s", exc)

    def _generate_quiz_bank(self) -> None:
        logger.info("Generating KNM quiz bank via ChatGPT in batches...")
        all_questions: list[dict[str, Any]] = []
        next_id = 1
        for category, count in QUIZ_BATCH_CATEGORIES:
            prompt = QUIZ_BATCH_PROMPT_TEMPLATE.format(
                count=count, category=category, start_id=next_id,
            )
            try:
                batch = self._generate_from_prompt(prompt)
                all_questions.extend(batch)
                next_id += len(batch)
                logger.info("Quiz batch '%s': got %d questions", category, len(batch))
            except Exception as exc:
                logger.error("Quiz batch '%s' failed: %s", category, exc)
        if all_questions:
            self._quiz_bank = all_questions
            self._save_json(QUIZ_BANK_PATH, self._quiz_bank)
            logger.info("Generated %d total quiz questions", len(self._quiz_bank))
        else:
            logger.error("Failed to generate any quiz questions")

    def _update_bank(self, bank_type: str, instruction: str) -> str:
        with self._lock:
            if bank_type == "vocab":
                bank, path = self._vocab_bank, VOCAB_BANK_PATH
            else:
                bank, path = self._quiz_bank, QUIZ_BANK_PATH
            current_json = json.dumps(bank, ensure_ascii=False, indent=2)
            prompt = UPDATE_BANK_PROMPT.format(
                bank_type=bank_type,
                instruction=instruction,
                current_bank=current_json,
            )
            try:
                updated = self._generate_from_prompt(prompt)
                if bank_type == "vocab":
                    self._vocab_bank = updated
                    self._sent_vocab_indices.clear()
                else:
                    self._quiz_bank = updated
                    self._sent_quiz_indices.clear()
                self._save_json(path, updated)
                return f"{bank_type.title()} bank updated! Now has {len(updated)} items."
            except Exception as exc:
                logger.error("Failed to update %s bank: %s", bank_type, exc)
                return f"Failed to update {bank_type} bank: {exc}"

    def regenerate_banks(self) -> str:
        with self._lock:
            self._generate_vocab_bank()
            self._generate_quiz_bank()
            self._sent_vocab_indices.clear()
            self._sent_quiz_indices.clear()
            return (
                f"Banks regenerated — vocab: {len(self._vocab_bank)}, "
                f"quiz: {len(self._quiz_bank)} items."
            )

    # ── Item selection ───────────────────────────────────────────────

    def _pick_revision(
        self, bank: list[dict[str, Any]], history: deque[tuple[int, float]]
    ) -> dict[str, Any] | None:
        now = time.time()
        cutoff = now - (REVISION_DELAY_HOURS * 3600)
        eligible = [
            idx for idx, sent_at in history
            if sent_at <= cutoff and idx < len(bank)
        ]
        if not eligible:
            return None
        priority = [
            idx for idx in eligible
            if bank[idx].get("category") in KNM_PRIORITY_CATEGORIES
            or bank[idx].get("difficulty") in KNM_PRIORITY_DIFFICULTIES
        ]
        pool = priority if priority else eligible
        return bank[random.choice(pool)]

    @staticmethod
    def _pick_new(
        bank: list[dict[str, Any]], sent: set[int]
    ) -> tuple[dict[str, Any] | None, set[int]]:
        if not bank:
            return None, sent
        available = [i for i in range(len(bank)) if i not in sent]
        if not available:
            sent = set()
            available = list(range(len(bank)))
        idx = random.choice(available)
        sent.add(idx)
        return bank[idx], sent

    # ── Message formatting ───────────────────────────────────────────

    @staticmethod
    def _format_vocab(item: dict[str, Any], is_revision: bool = False) -> str:
        dutch = item.get("dutch", "")
        english = item.get("english", "")
        word_type = item.get("type", "")
        example_nl = item.get("example_dutch", "")
        example_en = item.get("example_english", "")

        tag = "🔁" if is_revision else "🇳🇱"
        msg = f"{tag} *{dutch}*  →  {english}"
        if word_type:
            msg += f"  _({word_type})_"
        if example_nl:
            msg += f"\n📝 {example_nl}"
        if example_en:
            msg += f"\n    _{example_en}_"
        return msg

    @staticmethod
    def _format_quiz(item: dict[str, Any], is_revision: bool = False) -> str:
        q_nl = item.get("question_dutch", "")
        q_en = item.get("question_english", "")
        options = item.get("options", [])
        correct = item.get("correct_answer", "")
        explanation = item.get("explanation", "")
        category = item.get("category", "")
        difficulty = item.get("difficulty", "")
        pattern = item.get("pattern", "")

        tag = "🔁 *KNM Herhaling*" if is_revision else "📝 *KNM Examenvraag*"
        msg = f"🇳🇱 {tag}  [{category}] [{difficulty}] [{pattern}]\n\n"
        msg += f"*{q_nl}*\n_{q_en}_\n\n"
        for opt in options:
            label = opt.get("label", "")
            d = opt.get("dutch", "")
            e = opt.get("english", "")
            msg += f"  *{label}.* {d}  _{e}_\n"
        msg += f"\n✅ *{correct}*"
        if explanation:
            msg += f"  — {explanation}"
        return msg

    # ── Sending ──────────────────────────────────────────────────────

    def _pick_one_item(self) -> tuple[dict[str, Any] | None, bool, bool]:
        """Pick one item. Returns (item, is_vocab, is_revision)."""
        has_vocab = bool(self._vocab_bank)
        has_quiz = bool(self._quiz_bank)
        if not has_vocab and not has_quiz:
            return None, False, False

        if has_vocab and has_quiz:
            is_vocab = random.random() < VOCAB_WEIGHT
        elif has_vocab:
            is_vocab = True
        else:
            is_vocab = False

        if random.random() < REVISION_CHANCE:
            bank = self._vocab_bank if is_vocab else self._quiz_bank
            history = self._vocab_history if is_vocab else self._quiz_history
            item = self._pick_revision(bank, history)
            if item:
                return item, is_vocab, True

        if is_vocab:
            item, self._sent_vocab_indices = self._pick_new(
                self._vocab_bank, self._sent_vocab_indices
            )
            if item:
                idx = self._vocab_bank.index(item)
                self._vocab_history.append((idx, time.time()))
        else:
            item, self._sent_quiz_indices = self._pick_new(
                self._quiz_bank, self._sent_quiz_indices
            )
            if item:
                idx = self._quiz_bank.index(item)
                self._quiz_history.append((idx, time.time()))
        return item, is_vocab, False

    def send_message(self) -> bool:
        if not self._chat_id:
            logger.error("DUTCH_TUTOR_CHAT_ID is not configured")
            return False

        parts: list[str] = []
        labels: list[str] = []
        for _ in range(ITEMS_PER_MESSAGE):
            item, is_vocab, is_revision = self._pick_one_item()
            if not item:
                continue
            if is_vocab:
                parts.append(self._format_vocab(item, is_revision))
            else:
                parts.append(self._format_quiz(item, is_revision))
            kind = "revision" if is_revision else ("vocab" if is_vocab else "quiz")
            labels.append(f"{kind}:{item.get('dutch', item.get('id', ''))}")

        if not parts:
            logger.warning("No items picked, nothing to send")
            return False

        message = ("\n\n" + "─" * 30 + "\n\n").join(parts)

        try:
            self._whapi.send_text(to=self._chat_id, body=message)
            logger.info("Sent %d items: %s", len(labels), ", ".join(labels))
            return True
        except Exception as exc:
            logger.error("Failed to send message: %s", exc)
            return False

    # ── Scheduler: 10 random messages per hour ────────────────────────

    def _schedule_loop(self) -> None:
        while self._running:
            delays = sorted(
                random.uniform(0, ONE_HOUR_SECONDS) for _ in range(MESSAGES_PER_HOUR)
            )
            cycle_start = time.time()
            for delay in delays:
                wait = cycle_start + delay - time.time()
                if wait > 0 and self._running:
                    time.sleep(wait)
                if self._running:
                    self.send_message()

            remaining = cycle_start + ONE_HOUR_SECONDS - time.time()
            if remaining > 0 and self._running:
                time.sleep(remaining)

    def start(self) -> None:
        if self._running:
            logger.info("DutchTutor scheduler already running")
            return
        if not self._chat_id:
            logger.error("Cannot start DutchTutor: DUTCH_TUTOR_CHAT_ID not set")
            return
        self._running = True
        self._timer_thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self._timer_thread.start()
        logger.info(
            "DutchTutor started — sending to %s, %d messages every hour (75%% vocab, 25%% quiz)",
            self._chat_id,
            MESSAGES_PER_HOUR,
        )

    def stop(self) -> None:
        self._running = False
        logger.info("DutchTutor scheduler stopped")

    # ── Incoming message handler ─────────────────────────────────────

    def handle_incoming_message(self, text: str) -> None:
        lower = text.strip().lower()
        if not lower.startswith("dutch:"):
            return
        instruction = text.strip()[len("dutch:"):].strip()
        if not instruction:
            return

        cmd = instruction.lower()
        if cmd == "regenerate":
            result = self.regenerate_banks()
        elif cmd == "status":
            result = (
                f"DutchTutor running: {self._running}\n"
                f"Vocab bank: {len(self._vocab_bank)} items "
                f"({len(self._sent_vocab_indices)} sent this cycle)\n"
                f"Quiz bank: {len(self._quiz_bank)} items "
                f"({len(self._sent_quiz_indices)} sent this cycle)"
            )
        elif cmd == "send":
            self.send_message()
            result = "Sent a message now!"
        elif cmd.startswith("update vocab "):
            result = self._update_bank("vocab", instruction[len("update vocab "):])
        elif cmd.startswith("update quiz "):
            result = self._update_bank("quiz", instruction[len("update quiz "):])
        else:
            result = self._update_bank("vocab", instruction)

        try:
            self._whapi.send_text(to=self._chat_id, body=result)
        except Exception as exc:
            logger.error("Failed to send response: %s", exc)
