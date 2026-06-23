import instaloader

L = instaloader.Instaloader()
print("جاري محاولة تسجيل الدخول...")

try:
    # ضع كلمة المرور الصحيحة هنا
    L.login("panther.6059084", "Hh112233hh") 
    L.save_session_to_file("session-panther")
    print("✅ تم استخراج الجلسة بنجاح! ستجد ملفاً باسم 'session-panther'")
except Exception as e:
    print(f"❌ خطأ: {e}")
