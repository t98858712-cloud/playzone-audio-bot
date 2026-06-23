import instaloader

L = instaloader.Instaloader()

print("جاري محاولة تسجيل الدخول...")

try:
    # ضع كلمة المرور الخاصة بحسابك هنا بدلاً من النص العربي
    L.login("panther.6059084", "Hh112233hh") 
    
    # حفظ الجلسة في ملف
    L.save_session_to_file("session-panther")
    print("✅ تم تسجيل الدخول واستخراج ملف الجلسة (session-panther) بنجاح!")
    
except Exception as e:
    print(f"❌ حدث خطأ أثناء تسجيل الدخول: {e}")
