import instaloader

L = instaloader.Instaloader()
# ضع كلمة المرور الصحيحة والجديدة هنا
L.login("panther.6059084", "Hh112233hh") 
L.save_session_to_file("session-panther")
print("✅ تم حفظ ملف الجلسة بنجاح!")
