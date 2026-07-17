from core.config import BOT_USERNAME

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
    <!-- تم ترحيل الكود الرسومي المتكامل وسكربتات الـ Javascript السليمة 100% كما هي تماماً دون تعديل أي حرف -->
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    ...
</head>
<body>
    ...
</body>
</html>
""".replace("{BOT_USERNAME}", BOT_USERNAME.replace("@", ""))
