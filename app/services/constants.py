"""Константы для классификации (категории Paynet)."""
# Операторные ключевые слова для эскалации
OPERATOR_KEYWORDS = {"оператор", "человек", "operator", "inson", "odam"}

# Категории для классификации запросов (локальные ключевые слова + fallback OpenAI)
CATEGORIES = {
    "Претензия / Shikoyat": {
        "Оплата услуг через агента / Agent orqali xizmatlar uchun to'lov": [
            "агент не совершил платеж",
            "agent to'lovni amalga oshirmadi",
            "агент не выдал чек",
            "agent chek bermadi",
            "агент отказался оказывать услуги",
            "agent xizmat ko'rsatishdan bosh tortdi",
        ],
        "Оплата услуг через инфокиоск / Infokiosk orqali xizmatlar uchun to'lov": [
            "инфокиоск не выдал чек",
            "infokiosk chek bermadi",
            "infokiosk",
        ],
        "Оплата через Paynet Avia / Paynet Avia orqali to'lov": [
            "невозможно купить билет",
            "bilet sotib olishning iloji yo'q",
            "aviabilet",
        ],
        "Прочие жалобы / Boshqa shikoyatlar": [
            "отказ от использования мп paynet",
            "paynet mobil ilovasidan foydalanishdan bosh tortish",
            "не согласен с решением заявки",
            "ariza qaroriga rozi emasman",
            "мошенничество",
            "firibgarlik",
            "жалоба на сотрудника",
            "xodimdan shikoyat",
        ],
    },
    "Сервисное обслуживание / Xizmat ko'rsatish": {
        "Документы / Hujjatlar": [
            "предоставить чек оплаты",
            "to'lov chekini taqdim etish",
            "реквизиты компании",
            "kompaniya rekvizitlari",
        ]
    },
    "Вакансии / Vakansiyalar": {
        "Вакансии / Vakansiyalar": [
            "узнать о вакансиях",
            "vakansiyalar haqida bilish",
            "куда отправить резюме",
            "rezyumeni qayerga yuborish",
        ]
    },
    "Paynet Avia / Paynet Avia": {
        "Информация / Ma'lumot": [
            "отправка билета",
            "biletni jo'natish",
            "avia",
        ]
    },
    "Хулиганство / Bezorilik": {
        "Хулиганство / Bezorilik": [
            # short tokens matched with word-boundaries (avoid банкомат → мат)
            "мат",
            "сука",
            "бля",
            "fuck",
            "shit",
            "bo'ralab so'kinish",
            "хулиганство",
            "bezorilik",
        ]
    },
    "Предложения / Takliflar": {
        "Предложения / Takliflar": [
            "вопросы по инфокиоску",
            "infokiosk bo'yicha savollar",
            "прочие предложения",
            "boshqa takliflar",
            "хочет стать агентом",
            "agent bo'lishni xohlaydi",
            "хочет стать партнером",
            "hamkor bo'lishni xohlaydi",
        ]
    },
    "Информация / Ma'lumot": {
        "Акции / Aksiyalar": [
            "беспроцентный перевод",
            "foizsiz o'tkazma",
            "монеты",
            "tanga",
        ],
        "Запрос / So'rov": [
            "коммуникация",
            "kommunikatsiya",
        ],
        "Оплата услуг через агента / Agent orqali xizmatlar uchun to'lov": [
            "cashout",
            "cashout nfc",
            "отмена ошибочного платежа",
            "xato to'lovni bekor qilish",
            "пополнение humo",
            "humo",
            "пополнение uzcard",
            "uzcard",
            "проверка оплат",
            "to'lovlarni tekshirish",
            "результат претензии",
            "shikoyat natijasi",
        ],
        "Оплата услуг через инфокиоск / Infokiosk orqali xizmatlar uchun to'lov": [
            "cashin",
            "не отображается сумма",
            "pul balansi ko'rsatilmayapti",
            "не работает инфокиоск",
            "infokiosk ishlamayapti",
            "общая информация",
            "umumiy ma'lumot",
            "оплата через другие платежные инструменты",
            "boshqa to'lov vositalari orqali to'lov",
            "отмена ошибочного платежа",
            "xato to'lovni bekor qilish",
            "пополнение visa",
            "visa",
            "пополнение mastercard",
            "mastercard",
            "проверка оплат",
            "to'lovlarni tekshirish",
            "просит предоставить чек",
            "chek berishni so'raydi",
            "электронный кошелек",
            "elektron hamyon",
        ],
        "Оплата услуг через мобильное приложение / Mobil ilova orqali xizmatlar uchun to'lov": [
            "crypto",
            "p2p wallet",
            "paynet gold",
            "paynet nasiya",
            "visa direct",
            "identifikatsiya",
            "jd bilety",
            "poyezd chiptalari",
            "информация о бонусах и кешбек",
            "bonuslar va keshbek haqida ma'lumot",
            "не приходит sms",
            "sms kelmayapti",
            "общая информация о мобильном приложении",
            "mobil ilova haqida umumiy ma'lumot",
            "проверка p2p",
            "p2p tekshiruvi",
        ],
        "Оплата через Paynet Avia / Paynet Avia orqali to'lov": [
            "paynet avia",
            "общая информация",
            "umumiy ma'lumot",
            "результат претензии",
            "shikoyat natijasi",
        ],
        "Поставщики / Provayderlar": [
            "foyda",
            "общая информация",
            "umumiy ma'lumot",
        ],
    },
}
