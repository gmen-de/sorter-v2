<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';

	// The Save / Reset pair for a settings panel's footer. Rendered at both the
	// top and the bottom of long pages so the buttons are reachable without
	// scrolling: one component, so the two copies can never drift apart.
	let {
		save,
		reset,
		saving = false,
		dirty = false,
		disabled = false
	}: {
		save: () => void;
		reset: () => void;
		saving?: boolean;
		dirty?: boolean;
		disabled?: boolean;
	} = $props();
</script>

<div class="flex flex-1 flex-wrap items-center justify-end gap-2">
	{#if dirty}
		<span class="mr-auto text-sm text-warning-ink"
			>{$_('components.settings.settingsSaveBar.unsavedChanges')}</span
		>
	{/if}
	<Button variant="ghost" onclick={reset} disabled={saving || disabled}
		>{$_('components.settings.settingsSaveBar.resetButton')}</Button
	>
	<Button variant="primary" onclick={save} loading={saving} {disabled}
		>{$_('components.settings.settingsSaveBar.saveButton')}</Button
	>
</div>
