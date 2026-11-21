<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { settings } from '$lib/stores/settings';
	import { getCurrentThemeColorId, setThemeColor } from '$lib/stores/themeColor.svelte';
	import { locale, setLocale } from '$lib/i18n';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import ColorPicker from '$lib/components/ui/ColorPicker.svelte';
	import Sun from '@lucide/svelte/icons/sun';
	import Moon from '@lucide/svelte/icons/moon';

	let colorId = $state(getCurrentThemeColorId());
</script>

<Panel title={$_('components.settings.appearanceSection.panelTitle')} flush>
	<div class="divide-y divide-line">
		<SettingRow
			label={$_('components.settings.appearanceSection.theme.label')}
			help={$_('components.settings.appearanceSection.theme.help')}
		>
			<SegmentedControl
				label={$_('components.settings.appearanceSection.theme.label')}
				value={$settings.theme}
				onchange={(mode) => settings.setTheme(mode)}
				options={[
					{ value: 'light', label: $_('components.settings.appearanceSection.theme.light'), icon: Sun },
					{ value: 'dark', label: $_('components.settings.appearanceSection.theme.dark'), icon: Moon }
				]}
			/>
		</SettingRow>
		<SettingRow
			label={$_('components.settings.appearanceSection.language.label')}
			help={$_('components.settings.appearanceSection.language.help')}
		>
			<SegmentedControl
				label={$_('components.settings.appearanceSection.language.label')}
				value={$locale ?? 'en'}
				onchange={(loc) => setLocale(loc as 'en' | 'de')}
				options={[
					{ value: 'en', label: 'English' },
					{ value: 'de', label: 'Deutsch' }
				]}
			/>
		</SettingRow>
		<div class="px-(--pad-panel) py-(--pad-row)">
			<div class="text-sm font-medium text-ink">
				{$_('components.settings.appearanceSection.themeColor.title')}
			</div>
			<p class="mt-0.5 text-sm text-ink-muted">
				{$_('components.settings.appearanceSection.themeColor.description')}
			</p>
			<div class="mt-3 rounded-control bg-well p-4">
				<ColorPicker bind:value={colorId} onchange={(id) => void setThemeColor(id)} />
			</div>
		</div>
	</div>
</Panel>
