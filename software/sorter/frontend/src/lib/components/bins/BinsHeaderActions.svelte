<script lang="ts">
	import ArchiveX from '@lucide/svelte/icons/archive-x';
	import Download from '@lucide/svelte/icons/download';
	import FolderOutput from '@lucide/svelte/icons/folder-output';
	import History from '@lucide/svelte/icons/rotate-ccw-clock';
	import House from '@lucide/svelte/icons/house';
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';

	let {
		csvUrl,
		homing,
		emptyBusy,
		resetBusy,
		disabled,
		onSnapshots,
		onHome,
		onEmptyAll,
		onResetAll
	}: {
		csvUrl: string;
		homing: boolean;
		emptyBusy: boolean;
		resetBusy: boolean;
		disabled: boolean;
		onSnapshots: () => void;
		onHome: () => void;
		onEmptyAll: () => void;
		onResetAll: () => void;
	} = $props();
</script>

<!-- The page's actions, for PageHeader's `actions`. -->
<Button href={csvUrl} download icon={Download}>{$_('components.bins.binsHeaderActions.exportCsv')}</Button>
<Button icon={History} onclick={onSnapshots}>{$_('components.bins.binsHeaderActions.snapshots')}</Button>
<Button icon={House} loading={homing} {disabled} onclick={onHome}>
	{homing
		? $_('components.bins.binsHeaderActions.homingInProgress')
		: $_('components.bins.binsHeaderActions.homeButton')}
</Button>
<Button icon={FolderOutput} loading={emptyBusy} {disabled} onclick={onEmptyAll}>
	{emptyBusy
		? $_('components.bins.binsHeaderActions.emptyingInProgress')
		: $_('components.bins.binsHeaderActions.emptyAllBins')}
</Button>
<Button icon={ArchiveX} loading={resetBusy} {disabled} onclick={onResetAll}>
	{resetBusy
		? $_('components.bins.binsHeaderActions.resettingInProgress')
		: $_('components.bins.binsHeaderActions.resetAllBins')}
</Button>
