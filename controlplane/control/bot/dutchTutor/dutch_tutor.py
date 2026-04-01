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

GENERATE_KNM_VOCAB_PROMPT = (
    "Generate a JSON array of 200 Dutch words for the KNM (Kennis van de Nederlandse "
    "Maatschappij) exam and inburgering. Focus on official names and KNM society topics: "
    "government, gemeente, healthcare, work, education, taxes, rights, and public services. "
    "These must be the 200 most common MEDIUM/HARD words used in KNM preparation.\n\n"
    "WORD TYPES TO INCLUDE (balanced, KNM-focused):\n"
    "1. VERBS (exactly 25% = 50 verbs): Practical KNM verbs in infinitive form — "
    "aanvragen, inschrijven, inloggen, controleren, melden, regelen, invullen, "
    "ondertekenen, opbellen, afspreken, verhuizen, huren, betalen, ontvangen, "
    "solliciteren, stemmen, kiezen, besluiten, deelnemen, vaststellen, aanpassen, "
    "weigeren, verplichten, verbieden, toestaan, samenwerken, bespreken\n"
    "2. NOUNS: Official KNM terms and institutions — gemeente, Belastingdienst, UWV, "
    "DigiD, zorgverzekering, huisarts, ziekenhuis, apotheek, loon, contract, "
    "huurtoeslag, kinderbijslag, paspoort, rijbewijs, identiteitsbewijs, "
    "leerplicht, kinderopvang, inburgering, toeslagen, provincie, Tweede Kamer, "
    "Eerste Kamer, kabinet, minister-president, verkiezingen, grondwet, vrijheid, "
    "gelijkheid, discriminatie, integratie, samenleving\n"
    "3. ADJECTIVES/PHRASES: Society and civic life — verplicht, toegestaan, verboden, "
    "gelijkwaardig, verantwoordelijk, zelfstandig, officieel, tijdelijk, permanent, "
    "openbaar, particulier, schriftelijk, mondeling\n\n"
    "Each entry must be a JSON object with these keys:\n"
    '1. "dutch": the Dutch word or verb (infinitive for verbs)\n'
    '2. "english": English meaning\n'
    '3. "type": one of ["verb", "noun", "adjective", "phrase", "fact"]\n'
    '4. "example_dutch": a short A2-level Dutch sentence using the word\n'
    '5. "example_english": English translation of the sentence\n'
    '6. "category": one of ["healthcare", "children", "tax", "government", '
    '"society", "unemployment", "education", "country_knowledge", "daily_life"]\n'
    '7. "difficulty": one of ["medium", "hard"]\n\n'
    "Requirements:\n"
    "- Exactly 200 entries\n"
    "- Exactly 50 verbs (25%)\n"
    "- Only MEDIUM or HARD difficulty (no easy)\n"
    "- Focus on KNM exam language: society, rights, obligations, work, school, health, "
    "government, education, kids, rights, workplace\n"
    "- Use official names (e.g., Belastingdienst, UWV, DigiD, Tweede Kamer)\n"
    "- Use simple A2 Dutch in examples\n"
    "- Make examples realistic civic/municipal situations\n\n"
    "Return ONLY valid JSON. No markdown. No explanation.\n\n"
    "Example:\n"
    "[\n"
    "  {\n"
    '    "dutch": "aanvragen",\n'
    '    "english": "to apply for",\n'
    '    "type": "verb",\n'
    '    "example_dutch": "Ik moet een paspoort aanvragen bij de gemeente.",\n'
    '    "example_english": "I have to apply for a passport at the municipality.",\n'
    '    "category": "government",\n'
    '    "difficulty": "easy"\n'
    "  }\n"
    "]"
)

UPDATE_BANK_PROMPT = (
    "You are a Dutch KNM tutor assistant. The user wants to update a vocab bank. "
    "Bank type: {bank_type}\n\n"
    "Here is their instruction:\n\n{instruction}\n\n"
    "Here is the current bank:\n{current_bank}\n\n"
    "Apply the user's instruction to modify the bank. "
    "Return ONLY the complete updated JSON array (same format as input), no extra text."
)

ONE_HOUR_SECONDS = 60 * 60
MESSAGES_PER_HOUR = 6
ITEMS_PER_MESSAGE = 2
REVISION_DELAY_HOURS = 3
REVISION_CHANCE = 0.4
VOCAB_WEIGHT = 0.7  # 70% vocab, 30% quiz

KNM_PRIORITY_CATEGORIES = {
    "healthcare",
    "government",
    "children",
    "country_knowledge",
}
KNM_PRIORITY_DIFFICULTIES = {"hard"}


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
            with open(path, encoding="utf-8") as f:
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

        needs_vocab = not self._vocab_bank
        if needs_vocab:
            logger.info("Starting background vocab generation")
            thread = threading.Thread(target=self._background_generate, daemon=True)
            thread.start()
        else:
            self._banks_ready = True

    def _background_generate(self) -> None:
        self._generate_vocab_bank()
        self._banks_ready = True
        logger.info("Background bank generation complete (vocab=%d)", len(self._vocab_bank))

    def _generate_vocab_bank(self) -> None:
        logger.info("Generating KNM vocab bank via ChatGPT...")
        try:
            generated = self._generate_from_prompt(GENERATE_KNM_VOCAB_PROMPT)
            if self._vocab_bank:
                existing = {item.get("dutch", "").lower(): item for item in self._vocab_bank}
                for item in generated:
                    key = str(item.get("dutch", "")).lower()
                    if key and key not in existing:
                        self._vocab_bank.append(item)
                logger.info("Enriched vocab bank to %d items", len(self._vocab_bank))
            else:
                self._vocab_bank = generated
            self._save_json(VOCAB_BANK_PATH, self._vocab_bank)
            logger.info("Generated %d vocab items", len(self._vocab_bank))
        except Exception as exc:
            logger.error("Failed to generate vocab bank: %s", exc)

    def _update_bank(self, bank_type: str, instruction: str) -> str:
        with self._lock:
            if bank_type != "vocab":
                return "Quiz bank is disabled. Only vocab updates are supported."
            bank, path = self._vocab_bank, VOCAB_BANK_PATH
            current_json = json.dumps(bank, ensure_ascii=False, indent=2)
            prompt = UPDATE_BANK_PROMPT.format(
                bank_type=bank_type,
                instruction=instruction,
                current_bank=current_json,
            )
            try:
                updated = self._generate_from_prompt(prompt)
                self._vocab_bank = updated
                self._sent_vocab_indices.clear()
                self._save_json(path, updated)
                return f"{bank_type.title()} bank updated! Now has {len(updated)} items."
            except Exception as exc:
                logger.error("Failed to update %s bank: %s", bank_type, exc)
                return f"Failed to update {bank_type} bank: {exc}"

    def regenerate_banks(self) -> str:
        with self._lock:
            self._generate_vocab_bank()
            self._sent_vocab_indices.clear()
            return f"Vocab bank regenerated — {len(self._vocab_bank)} items."

    # ── Item selection ───────────────────────────────────────────────

    def _pick_revision(self, bank: list[dict[str, Any]], history: deque[tuple[int, float]]) -> dict[str, Any] | None:
        now = time.time()
        cutoff = now - (REVISION_DELAY_HOURS * 3600)
        eligible = [idx for idx, sent_at in history if sent_at <= cutoff and idx < len(bank)]
        if not eligible:
            return None
        priority = [
            idx
            for idx in eligible
            if bank[idx].get("category") in KNM_PRIORITY_CATEGORIES
            or bank[idx].get("difficulty") in KNM_PRIORITY_DIFFICULTIES
        ]
        pool = priority if priority else eligible
        return bank[random.choice(pool)]  # nosec B311

    @staticmethod
    def _pick_new(bank: list[dict[str, Any]], sent: set[int]) -> tuple[dict[str, Any] | None, set[int]]:
        if not bank:
            return None, sent
        available = [i for i in range(len(bank)) if i not in sent]
        if not available:
            sent = set()
            available = list(range(len(bank)))
        idx = random.choice(available)  # nosec B311
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
            is_vocab = random.random() < VOCAB_WEIGHT  # nosec B311
        elif has_vocab:
            is_vocab = True
        else:
            is_vocab = False

        if random.random() < REVISION_CHANCE:  # nosec B311
            bank = self._vocab_bank if is_vocab else self._quiz_bank
            history = self._vocab_history if is_vocab else self._quiz_history
            item = self._pick_revision(bank, history)
            if item:
                return item, is_vocab, True

        if is_vocab:
            item, self._sent_vocab_indices = self._pick_new(self._vocab_bank, self._sent_vocab_indices)
            if item:
                idx = self._vocab_bank.index(item)
                self._vocab_history.append((idx, time.time()))
        else:
            item, self._sent_quiz_indices = self._pick_new(self._quiz_bank, self._sent_quiz_indices)
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
                random.uniform(0, ONE_HOUR_SECONDS)  # nosec B311
                for _ in range(MESSAGES_PER_HOUR)
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
        instruction = text.strip()[len("dutch:") :].strip()
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
            result = self._update_bank("vocab", instruction[len("update vocab ") :])
        elif cmd.startswith("update quiz "):
            result = self._update_bank("quiz", instruction[len("update quiz ") :])
        else:
            result = self._update_bank("vocab", instruction)

        try:
            if self._chat_id:
                self._whapi.send_text(to=self._chat_id, body=result)
        except Exception as exc:
            logger.error("Failed to send response: %s", exc)
