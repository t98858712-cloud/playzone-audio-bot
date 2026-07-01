const GuildConfig = require('../schemas/GuildSchema');

module.exports = {
    name: 'interactionCreate',
    async execute(interaction, client) {
        // إنشاء إعدادات افتراضية للسيرفر إذا لم تكن موجودة
        let guildConfig = await GuildConfig.findOne({ guildId: interaction.guild.id });
        if (!guildConfig) {
            guildConfig = await GuildConfig.create({ guildId: interaction.guild.id });
        }

        // --- معالجة الأوامر ---
        if (interaction.isChatInputCommand()) {
            const command = client.commands.get(interaction.commandName);
            if (!command) return;

            // نظام حماية الصلاحيات (RBAC)
            if (command.requiredRole) {
                const userRoles = interaction.member.roles.cache;
                const allowedRoles = guildConfig.roles[command.requiredRole] || [];
                
                const hasPermission = interaction.member.permissions.has('Administrator') || allowedRoles.some(roleId => userRoles.has(roleId));
                
                if (!hasPermission) {
                    return interaction.reply({ content: '❌ ليس لديك الصلاحية لاستخدام هذا الأمر في نظام الإدارة.', ephemeral: true });
                }
            }

            try {
                await command.execute(interaction, client, guildConfig);
            } catch (error) {
                console.error(error);
                if (interaction.replied || interaction.deferred) {
                    await interaction.followUp({ content: 'حدث خطأ أثناء تنفيذ الأمر.', ephemeral: true });
                } else {
                    await interaction.reply({ content: 'حدث خطأ أثناء تنفيذ الأمر.', ephemeral: true });
                }
            }
        }

        // --- معالجة الأزرار الذكية ---
        else if (interaction.isButton()) {
            // المعرف الذكي: actionType:actionName:entityId:targetUserId
            const [actionType, actionName, entityId, targetUserId] = interaction.customId.split(':');

            const buttonCommand = client.buttons.get(actionType);
            if (!buttonCommand) return;

            try {
                await buttonCommand.execute(interaction, client, { actionName, entityId, targetUserId }, guildConfig);
            } catch (error) {
                console.error(error);
            }
        }
    }
};
