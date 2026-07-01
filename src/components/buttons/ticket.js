module.exports = {
    id: 'ticket', // المعرف الأساسي للزر
    
    async execute(interaction, client, data, guildConfig) {
        const { actionName, entityId, targetUserId } = data;

        // التحقق من الصلاحيات بناءً على الإجراء
        if (actionName === 'close') {
            const userRoles = interaction.member.roles.cache;
            const supportRoles = guildConfig.roles.support || [];
            const isSupport = supportRoles.some(roleId => userRoles.has(roleId)) || interaction.member.permissions.has('Administrator');

            // إذا لم يكن صاحب التذكرة ولم يكن من الدعم الفني المعتمد
            if (interaction.user.id !== targetUserId && !isSupport) {
                return interaction.reply({ content: '❌ هذا الإجراء مخصص للدعم الفني أو صاحب التذكرة فقط.', ephemeral: true });
            }

            await interaction.reply({ content: `🔒 جاري إغلاق التذكرة رقم ${entityId}...` });
            // هنا تضع كود أرشفة القناة وحذفها
        }
    }
};
