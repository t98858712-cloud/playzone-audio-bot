const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');

module.exports = {
    data: new SlashCommandBuilder()
        .setName('setrole')
        .setDescription('تعيين رتبة لقسم إداري معين في النظام (مستوى الشركات)')
        .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
        .addStringOption(option =>
            option.setName('department')
                .setDescription('القسم الإداري')
                .setRequired(true)
                .addChoices(
                    { name: 'الإدارة العليا (Admin)', value: 'admin' },
                    { name: 'الدعم الفني (Support)', value: 'support' },
                    { name: 'القسم المالي (Finance)', value: 'finance' }
                ))
        .addRoleOption(option =>
            option.setName('role')
                .setDescription('الرتبة التي ستحصل على الصلاحية')
                .setRequired(true)),
    
    // هذا الأمر متاح فقط لمن لديه صلاحية Administrator (كما حددنا أعلاه)
    async execute(interaction, client, guildConfig) {
        const department = interaction.options.getString('department');
        const role = interaction.options.getRole('role');

        // إضافة الرتبة إلى القسم المحدد في قاعدة البيانات
        if (!guildConfig.roles[department].includes(role.id)) {
            guildConfig.roles[department].push(role.id);
            await guildConfig.save();
            await interaction.reply({ content: `✅ تم منح رتبة ${role.name} صلاحيات قسم: **${department}** بنجاح.`, ephemeral: true });
        } else {
            await interaction.reply({ content: `⚠️ هذه الرتبة تمتلك صلاحيات هذا القسم مسبقاً.`, ephemeral: true });
        }
    },
};
