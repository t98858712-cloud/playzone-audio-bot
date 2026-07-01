require('dotenv').config();
const { Telegraf, Markup } = require('telegraf');

// تهيئة البوت باستخدام التوكن من ملف .env
const bot = new Telegraf(process.env.TELEGRAM_TOKEN);
const ADMIN_ID = process.env.ADMIN_ID;

// === أوامر البوت (Commands) ===
bot.start((ctx) => {
    // إنشاء زر شفاف (Inline Button)
    const keyboard = Markup.inlineKeyboard([
        Markup.button.callback('🛠️ فتح لوحة تحكم الإدارة', 'open_dashboard')
    ]);

    ctx.reply('مرحباً بك! هذا النظام مخصص لإدارة أعمال الشركة.', keyboard);
});

// === التفاعلات والأزرار (Buttons & Actions) ===
bot.action('open_dashboard', (ctx) => {
    // التحقق من صلاحيات الإدارة
    const userId = ctx.from.id.toString();

    if (userId === ADMIN_ID) {
        ctx.reply('✅ أهلاً بك أيها المدير. لديك الصلاحية الكاملة للتحكم بالنظام.');
        // يمكننا لاحقاً إضافة أزرار إضافية تظهر للأدمن فقط هنا
    } else {
        ctx.reply('❌ عذراً، هذا الزر مخصص للإدارة العليا فقط ولا تملك صلاحية للوصول إليه.');
    }
    
    // إخفاء علامة "التحميل" من الزر بعد الضغط عليه
    ctx.answerCbQuery();
});

// تشغيل البوت
bot.launch().then(() => {
    console.log('✅ تم تشغيل بوت التيليجرام بنجاح!');
});

// إيقاف البوت بأمان عند إغلاق السيرفر
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
