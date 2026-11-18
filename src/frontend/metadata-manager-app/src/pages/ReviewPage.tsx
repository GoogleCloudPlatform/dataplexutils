/*
Copyright 2025 Google LLC

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
*/

import React, { useState, useEffect, useRef, KeyboardEvent as ReactKeyboardEvent } from 'react';
import {
  Box,
  Paper,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  IconButton,
  Chip,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Alert,
  ToggleButtonGroup,
  ToggleButton,
  Grid,
  Card,
  CardContent,
  List,
  ListItem,
  ListItemText,
  Divider,
  Tooltip,
  CircularProgress,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import EditIcon from '@mui/icons-material/Edit';
import RefreshIcon from '@mui/icons-material/Refresh';
import NavigateNextIcon from '@mui/icons-material/NavigateNext';
import NavigateBeforeIcon from '@mui/icons-material/NavigateBefore';
import ViewListIcon from '@mui/icons-material/ViewList';
import RateReviewIcon from '@mui/icons-material/RateReview';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import LaunchIcon from '@mui/icons-material/Launch';
import axios from 'axios';
import ReactQuill from 'react-quill';
import 'react-quill/dist/quill.snow.css';
import FormControlLabel from '@mui/material/FormControlLabel';
import Switch from '@mui/material/Switch';
import { DatplexConfig } from '../App';
import { useCache } from '../contexts/CacheContext';
import { useReview, MetadataItem as ReviewContextMetadataItem } from '../contexts/ReviewContext';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import VisibilityIcon from '@mui/icons-material/Visibility';
import { extractApiErrorMessage, getApiBaseUrl, getAxiosRequestConfig } from '../utils/apiClient';
import { escapeHtmlText, sanitizeHtml, stripHtmlToText } from '../utils/sanitizeHtml';

interface Comment {
  id: string;
  text: string;
  type: string;
  timestamp: string;
}

interface EditorChangeHandler {
  (content: string): void;
}

interface ReviewPageProps {
  config: {
    project_id: string;
    llm_location: string;
    dataplex_location: string;
    dataset_id?: string;
    description_handling?: string;
    description_prefix?: string;
  };
  apiToken?: string;
}

// Extend the MetadataItem interface
interface MetadataItem extends ReviewContextMetadataItem {
  isAccepted?: boolean; // Added optional field
  whenAccepted?: string; // Added optional field
  isMarkingForRegeneration?: boolean; // Keep this
  currentColumn?: MetadataItem | null; // Ensure recursive type is correct
  metadata?: { [key: string]: any }; // Add metadata explicitly if not present in base type
  columns?: any[]; // +++ Add optional columns array for cached table data +++
}

const ReviewPage: React.FC<ReviewPageProps> = ({ config, apiToken }) => {
  const { detailsCache, setDetailsCache, clearCache } = useCache();
  const { state, dispatch } = useReview();
  const { items = [], pageToken, totalCount, currentItemIndex, viewMode, hasLoadedItems } = state;

  const [editItem, setEditItem] = useState<MetadataItem | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [newComment, setNewComment] = useState('');
  const [commentType, setCommentType] = useState<'human' | 'negative'>('human');
  const [apiUrlBase, setApiUrlBase] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [isRichText, setIsRichText] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isLoadingNext, setIsLoadingNext] = useState(false);
  const [showDiff, setShowDiff] = useState(true);
  const editorRef = useRef<any>(null);
  const [isAccepting, setIsAccepting] = useState<{ [key: string]: boolean }>({});
  const [isLoading, setIsLoading] = useState(false);
  const [currentItemId, setCurrentItemId] = useState<string | null>(null);
  const [taggedColumns, setTaggedColumns] = useState<MetadataItem[]>([]);
  const [currentColumnIndex, setCurrentColumnIndex] = useState<number>(-1);
  const [isColumnView, setIsColumnView] = useState(false);
  const listContainerRef = useRef<HTMLDivElement>(null);
  const [notification, setNotification] = useState<{ message: string; severity: 'success' | 'error' | 'info'; loading: boolean } | null>(null);
  const [isColumnDataReady, setIsColumnDataReady] = useState(true); // <-- Add state for column readiness
  // const intendedColumnIdRef = useRef<string | null>(null); // --- Remove Ref ---
  const hasManuallySetColumnFlag = useRef(false);

  // Initialize API URL
  useEffect(() => {
    setApiUrlBase(getApiBaseUrl());
  }, []);

  const normalizedBaseUrl = () => (apiUrlBase || getApiBaseUrl()).replace(/\/+$/, '');

  // Sync column navigation state with current item
  useEffect(() => {
    // +++ Add logging +++
    console.log('[useEffect Sync Columns] Running. Dependencies:', { viewMode, currentItemIndex, currentItemId, hasItems: items.length > 0, cacheKeys: Object.keys(detailsCache).length });

    if (viewMode === 'review' && items.length > 0 && currentItemIndex >= 0) {
      const currentItem = items[currentItemIndex];

      if (currentItem) {
        // If this is a table item ID in the main list (doesn't guarantee we are *viewing* the table)
        // Let's refine this check later if needed
        if (!currentItem.id.includes('#column.')) { 
          const tableId = currentItem.id; // This is the ID of the parent table item
          // +++ Add logging +++
          console.log('[useEffect Sync Columns] Processing Table View Sync Logic for parent table item:', tableId);
          
          // Sync currentItemId if it doesn't match the main table item ID
          // This might be needed if navigation landed here improperly
          if (currentItemId !== tableId) {
            console.log(`Syncing currentItemId to table: ${tableId}`);
            console.log(`[useEffect Sync Columns] Updating currentItemId from ${currentItemId} to ${tableId}`);
            setCurrentItemId(tableId);
          }
          
          // Sync taggedColumns based on the TABLE's cache entry
          const tableCache = detailsCache[tableId]; 
          if (tableCache?.columns) { 
            console.log(`[useEffect Sync Columns] Found columns in cache for ${tableId}. Processing ${tableCache.columns.length} columns.`);
            const columnsWithMetadata = tableCache.columns.filter((col: any) => 
              col.draftDescription || col.currentDescription || 
              (col.metadata && Object.keys(col.metadata).length > 0)
            );
            
            if (taggedColumns.length !== columnsWithMetadata.length) { 
                console.log('[useEffect Sync Columns] Syncing taggedColumns based on table cache. New count:', columnsWithMetadata.length);
                setTaggedColumns(columnsWithMetadata);
            } else {
                 console.log('[useEffect Sync Columns] taggedColumns length matches cache. No update needed.');
            }
          } else {
             console.log(`[useEffect Sync Columns] No columns found in cache for ${tableId}. taggedColumns length: ${taggedColumns.length}`);
             // If cache is missing columns, ensure taggedColumns is empty
             if (taggedColumns.length > 0) {
                console.log('[useEffect Sync Columns] Clearing taggedColumns as table cache is missing them.');
                setTaggedColumns([]);
             }
          }
        } else {
            console.log('[useEffect Sync Columns] Current item ID in items list includes #column. Skipping table sync logic for ID:', currentItem.id);
        }
      }
    }
    // Remove currentItemId from deps for now to test if it prevents unwanted runs
  }, [viewMode, items, currentItemIndex, detailsCache, dispatch]); // Added dispatch as it's used indirectly via setCurrentItemId potentially

  // Add keyboard navigation
  useEffect(() => {
    const handleKeyPress = (event: ReactKeyboardEvent) => {
      if (viewMode === 'review') {
        if (event.key === 'ArrowRight' || event.key === 'j') {
          handleNext();
        } else if (event.key === 'ArrowLeft' || event.key === 'k') {
          handlePrevious();
        }
      }
    };

    window.addEventListener('keydown', handleKeyPress as unknown as EventListener);
    return () => {
      window.removeEventListener('keydown', handleKeyPress as unknown as EventListener);
    };
  }, [viewMode, currentItemIndex, items.length]);

  // Add a useEffect to track state changes for debugging
  useEffect(() => {
    console.log('State change detected:', {
      viewMode,
      currentItemIndex,
      currentItemId,
      isColumnView: items[currentItemIndex]?.currentColumn ? true : false,
      taggedColumnsCount: taggedColumns.length,
      currentColumnIndex
    });
  }, [viewMode, currentItemIndex, currentItemId, items, taggedColumns, currentColumnIndex]);

  // Load items if not loaded
  useEffect(() => {
    if (apiUrlBase && config.project_id && !hasLoadedItems) {
      setIsLoading(true); // Set main loading true
      console.log('[useEffect Initial Load] Triggering fetchReviewItems'); // <-- Add log
      fetchReviewItems().then(async (success) => { // make async to await loadItemDetails
        if (success && viewMode === 'review' && state.items.length > 0) {
          console.log('[useEffect Initial Load] fetchReviewItems succeeded (review mode), loading initial details'); // <-- Add log
          // Load initial item details if in review mode after fetch
          try {
            setIsLoadingDetails(true);
            await loadItemDetails(state.items[0].id); // Not a forceRefresh
            // Preload after successful load
            if (state.items.length > 1) {
              preloadNextItem(0);
            }
          } catch (e) {
            console.error("Error loading initial item details:", e);
            setError("Failed to load initial item details");
          } finally {
            setIsLoadingDetails(false); // Unset details loading
          }
        }
      }).catch((e) => {
          console.error('[useEffect Initial Load] fetchReviewItems failed:', e); // <-- Add log
          console.error("Error fetching initial items:", e);
          setError("Failed to fetch initial review items");
      }).finally(() => {
        setIsLoading(false); // Unset main loading
        dispatch({ type: 'SET_HAS_LOADED', payload: true });
      });
    }
  // Corrected dependencies: dispatch and state.items.length (to trigger load after items arrive)
  }, [apiUrlBase, config.project_id, hasLoadedItems, viewMode, dispatch, state.items.length]);

  const handleViewModeChange = (event: React.MouseEvent<HTMLElement>, newMode: 'list' | 'review' | null) => {
    if (newMode) {
      dispatch({ type: 'SET_VIEW_MODE', payload: newMode });
      // If switching to review mode and items exist, load the current (likely first) item
      if (newMode === 'review' && items.length > 0) {
        const itemToLoad = items[currentItemIndex]; // Load current index
        if (itemToLoad) {
          // Load details if not already cached (loadItemDetails handles check)
          const loadCurrentItem = async () => {
            try {
              setIsLoadingDetails(true);
              await loadItemDetails(itemToLoad.id); // Not a forceRefresh
            preloadNextItem(currentItemIndex);
            } catch (e) {
              console.error('Error loading details on view mode change:', e);
              // setError handled by loadItemDetails
            } finally {
              setIsLoadingDetails(false);
          }
          };
          loadCurrentItem();
        }
      }
    }
  };

  const handleRefresh = async () => {
    setError(null);

    // Always use the currentItemId (should represent the current TABLE context)
    const tableIdToRefresh = currentItemId;

    // +++ Add logging +++
    console.log('🔍 REFRESH START - State check BEFORE logic:', { // <-- Add this log
        viewMode,
        currentItemId: currentItemId, // Log the component's state variable
        tableIdToRefresh: tableIdToRefresh, // Log the variable used in the function
    });
    // +++ End Add logging +++

    console.log('🔍 REFRESH START - Current state:', {
        viewMode,
        currentItemId: tableIdToRefresh, // Log the ID we intend to refresh
    });

    if (viewMode === 'list') {
      console.log('🔍 REFRESH - List mode');
        setIsRefreshing(true);
      try {
        await fetchReviewItems();
        setNotification({ message: 'Review list refreshed', severity: 'success', loading: false });
        setTimeout(() => setNotification(null), 3000);
      } catch (e) {
        console.error('🔍 REFRESH - List mode error:', e);
        setError('Failed to refresh review list');
        setNotification({ message: 'Failed to refresh list', severity: 'error', loading: false });
        setTimeout(() => setNotification(null), 5000);
      } finally {
        setIsRefreshing(false);
      }
    } else if (viewMode === 'review') {
      // +++ START: Move the check for tableIdToRefresh inside the review mode block +++
      if (!tableIdToRefresh) {
          console.warn('🔍 REFRESH - Cannot refresh, no valid Table ID (currentItemId) determined.');
          setNotification({ message: 'Cannot refresh: current table not identified.', severity: 'info', loading: false });
          setTimeout(() => setNotification(null), 3000);
          return; // Exit if no ID for review mode
      }
      // +++ END: Move the check +++
      console.log('🔍 REFRESH - Review mode, refreshing TABLE:', tableIdToRefresh);
      setNotification({ message: 'Refreshing current table metadata...', severity: 'info', loading: true });

      try {
        // Always call loadItemDetails with the TABLE ID
        const refreshedDetails = await loadItemDetails(tableIdToRefresh, true); // forceRefresh = true

        if (refreshedDetails) {
          console.log('🔍 REFRESH - Review mode completed for TABLE:', tableIdToRefresh);
          setNotification({ message: 'Current table refreshed successfully', severity: 'success', loading: false });
          setTimeout(() => setNotification(null), 3000);
          // Preload next TABLE item
          if (currentItemIndex < items.length - 1) {
             preloadNextItem(currentItemIndex);
          }
        } else {
          console.warn('🔍 REFRESH - Review mode: loadItemDetails returned null/error for', tableIdToRefresh);
          // setError handled by loadItemDetails
          setNotification({ message: 'Failed to refresh current item', severity: 'error', loading: false });
          setTimeout(() => setNotification(null), 5000);
      }
    } catch (error) {
        // Catch errors from axios call itself if loadItemDetails fails internally
        console.error('🔍 REFRESH - Review mode error during await:', error);
        const errorMessage = error instanceof Error ? error.message : 'Failed to refresh current item details';
        setError(errorMessage); // Set error state
        setNotification({ message: `Error refreshing current item: ${errorMessage}`, severity: 'error', loading: false });
        setTimeout(() => setNotification(null), 5000);
    } finally {
        // Ensure setIsLoadingDetails is reset since loadItemDetails(forceRefresh=true) sets it
        setIsLoadingDetails(false); // Ensure loading indicator is turned off
        setNotification(prev => prev ? { ...prev, loading: false } : null);
      }
    } else {
      console.warn('🔍 REFRESH - Called in unexpected state', { viewMode, tableIdToRefresh });
    }
  };

  const handleViewDetails = async (itemId: string) => {
    const itemIndex = items.findIndex((item) => item.id === itemId);
    if (itemIndex !== -1) {
      dispatch({ type: 'SET_CURRENT_INDEX', payload: itemIndex });
      dispatch({ type: 'SET_VIEW_MODE', payload: 'review' });
      
      try {
        setIsLoadingDetails(true); // Set loading before await
        await loadItemDetails(itemId); // forceRefresh is false by default
        preloadNextItem(itemIndex);
      } catch (error) {
        console.error('Error in handleViewDetails:', error);
        // setError should be handled within loadItemDetails
      } finally {
        setIsLoadingDetails(false); // Ensure loading is unset
      }
    }
  };

  const handleAccept = async (item: MetadataItem) => {
    try {
      // Store current view state before making any changes
      const wasInColumnView = isColumnView;
      const previousColumnIndex = currentColumnIndex;
      const isViewingColumn = item.type === 'column' || Boolean(item.currentColumn);
      const currentlyViewedItemId = currentItemId;
      
      console.log('handleAccept - Starting with state:', {
        itemId: item.id,
        wasInColumnView,
        previousColumnIndex,
        isViewingColumn,
        itemType: item.type,
        currentlyViewedItemId
      });
      
      // Show notification
      setNotification({
        message: `Accepting ${isViewingColumn ? 'column' : 'table'} metadata...`,
        severity: 'info',
        loading: true
      });
      
      // Store the item ID that is currently being displayed
      const initiallyDisplayedItemId = isColumnView ? 
        (currentItemId && currentColumnIndex >= 0 ? 
          `${currentItemId}#column.${taggedColumns[currentColumnIndex]?.name?.split('.').pop()}` : 
          currentItemId) : 
        currentItemId;
      console.log('[handleAccept] Initially displayed item ID:', initiallyDisplayedItemId);
      
      // Clear any existing accepting state first to ensure buttons work properly
      setIsAccepting({});
      
      // Then set accepting state for just this item
      setIsAccepting(prev => ({ ...prev, [item.id]: true }));
      
      setError(null);
      
      const tableFqn = item.id.split('#')[0];
      const [projectId, datasetId, tableId] = tableFqn.split('.');
      const isColumn = item.type === 'column' || isViewingColumn;
      const columnName = isColumn ? (item.id.includes('#column.') ? item.id.split('#column.')[1] : item.name.split('.').pop()) : undefined;
      
      console.log('handleAccept - Determined item details:', {
        isColumn,
        columnName,
        tableFqn
      });
      
      const endpoint = isColumn ? 'accept_column_draft_description' : 'accept_table_draft_description';
      
      await axios.post(
        `${normalizedBaseUrl()}/${endpoint}`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          client_options_settings: {
            use_lineage_tables: false,
            use_lineage_processes: false,
            use_profile: false,
            use_data_quality: false,
            use_ext_documents: false,
            persist_to_dataplex_catalog: true,
            stage_for_review: false,
            dry_run: false,
            top_values_in_description: true,
            description_handling: config.description_handling || 'append',
            description_prefix: config.description_prefix || '---AI Generated description---'
          },
          table_settings: {
            project_id: projectId,
            dataset_id: datasetId,
            table_id: tableId,
            documentation_uri: item.metadata?.external_document_uri || item.externalDocumentUri || ""
          },
          dataset_settings: {
            project_id: projectId,
            dataset_id: datasetId,
            documentation_csv_uri: "",
            strategy: "NAIVE"
          },
          column_settings: isColumn ? {
              column_name: columnName
          } : undefined,
          column_name: columnName
        },
        getAxiosRequestConfig(apiToken)
      );

      // Check if the user navigated away while the API call was in progress
      const currentlyDisplayedItemIdAfterAPI = isColumnView ? 
        (currentItemId && currentColumnIndex >= 0 ? 
          `${currentItemId}#column.${taggedColumns[currentColumnIndex]?.name?.split('.').pop()}` : 
          currentItemId) : 
        currentItemId;
      const userNavigatedAway = initiallyDisplayedItemId !== currentlyDisplayedItemIdAfterAPI;
      console.log('[handleAccept] Navigation check after API:', { 
        initial: initiallyDisplayedItemId, 
        current: currentlyDisplayedItemIdAfterAPI, 
        userNavigatedAway 
      });

      // Update both state and cache with the accepted status
      const updatedItem: Partial<MetadataItem> = {
        status: 'accepted' as const,
        currentDescription: item.draftDescription, // Update current description in UI
        whenAccepted: new Date().toISOString(),
        isAccepted: true // <-- Add this line
      };

      // Update the item in state without changing the current view
       // --- START: Update state correctly for columns ---
       if (isColumn && currentlyViewedItemId && currentlyViewedItemId === tableFqn + '#table') {
         // If we are viewing the parent table, update its currentColumn
         // Find the current parent table item in state
         const parentTableItem = items.find(i => i.id === currentlyViewedItemId);
         const baseColumnData = parentTableItem?.currentColumn;

         if (!parentTableItem || !baseColumnData) {
           console.error('[handleAccept] Could not find base currentColumn data in state for', currentlyViewedItemId);
           setError('Internal error: Could not update column state.');
           setNotification({ message: 'Internal error: Failed to update UI state.', severity: 'error', loading: false });
           setTimeout(() => setNotification(null), 5000);
           // Revert accepting state if needed
           setTimeout(() => { setIsAccepting(prev => ({ ...prev, [item.id]: false })); }, 500);
           return; // Stop execution
         }

         // Construct the updated column data based on existing state + acceptance changes
         // Ensure it conforms to the expected type (ReviewContextMetadataItem)
         const updatedCurrentColumnData: ReviewContextMetadataItem = {
           ...baseColumnData, // Start with existing currentColumn data from state
           // Apply acceptance updates selectively
           status: 'accepted', // Explicitly set status
           currentDescription: item.draftDescription, // Update description from the accepted item
           lastModified: new Date().toISOString(), // Update timestamp
           // Note: We are NOT merging the full local `updatedItem` here
           // as it might contain fields not present in ReviewContextMetadataItem (like isAccepted)
           // The UI should rely on navigation/refresh logic (using cache) to get the full details including isAccepted.
         };

         console.log('[handleAccept] Updating parent table item in state:', currentlyViewedItemId, 'with new currentColumn data:', updatedCurrentColumnData);

         // --- REMOVED Dispatch for currentColumn update on column accept --- 
         // Let the cache update handle reflecting the change on next load.
         console.log(`[handleAccept] Column ${item.id} accepted. Skipping immediate state dispatch for currentColumn.`);

         // +++ ADD: Conditional state update for parent table +++
         if (!userNavigatedAway) {
           console.log('[handleAccept] User did not navigate away. Updating parent table state with new column details.');
           dispatch({
             type: 'UPDATE_ITEM',
             payload: {
               id: currentlyViewedItemId, // Parent Table ID
               updates: { currentColumn: updatedCurrentColumnData } // Update its currentColumn
             }
           });
         } else {
           console.log('[handleAccept] User navigated away. Not updating parent table state display.');
         }

       } else {
          // Original dispatch for tables or standalone column updates
          console.log('[handleAccept] Updating item directly in state:', item.id);
          // Ensure updatedItem only contains fields compatible with the reducer's expected type for Partial<MetadataItem>
          // Assuming the reducer primarily cares about status and lastModified here for direct updates
          const compatibleUpdates: Partial<MetadataItem> = {
             status: 'accepted',
             currentDescription: updatedItem.currentDescription, // Keep this update
             lastModified: updatedItem.whenAccepted || new Date().toISOString(),
             // Avoid adding fields like isAccepted if not part of ReviewContextMetadataItem // <-- REVISING THIS
             isAccepted: true // +++ Ensure isAccepted is included in the table state update +++
          };
          dispatch({
            type: 'UPDATE_ITEM',
            payload: {
              id: item.id, // Table ID or specific Column ID
              updates: compatibleUpdates // Send only compatible fields
            }
          });
       }
       // --- END: Update state correctly for columns ---

       // +++ START: Log after successful column accept API call +++
       if (isColumn) {
         console.log(`[handleAccept] Successfully completed API call for accepting column: ${item.id}. About to update state/cache.`);
       }
       // +++ END: Log after successful column accept API call +++

      // Update cache
      setDetailsCache(prev => {
        const newCache = { ...prev };
        const previousCacheEntry = newCache[item.id]; // Get previous state for the specific item
        
        // If user navigated away during the process, don't update the content properties
        // Only update status flags and metadata, not the content that would appear on screen
        let updatedCacheEntry;
        if (userNavigatedAway) {
          console.log('[handleAccept] User navigated away, updating status only in cache to prevent content replacement');
          updatedCacheEntry = {
            ...previousCacheEntry,
            status: 'accepted',
            isAccepted: true,
            whenAccepted: new Date().toISOString(),
            // Explicitly NOT updating currentDescription to avoid content replacement
          };
        } else {
          updatedCacheEntry = {
            ...previousCacheEntry,
            ...updatedItem // Apply all acceptance updates including content
          };
        }
 
        // +++ Log cache update +++
        console.log(`[handleAccept] Updating cache for ${item.id}.`);
        console.log(`[handleAccept] Cache - Previous state:`, previousCacheEntry);
        console.log(`[handleAccept] Cache - Updates being applied:`, userNavigatedAway ? 'Status only' : updatedItem);
        console.log(`[handleAccept] Cache - New entry state:`, updatedCacheEntry);
        // +++ END Log +++
 
        newCache[item.id] = updatedCacheEntry; // Set the updated entry in the new cache object
        return newCache; // Return the entire updated cache
      });
      
      // Once the accept is completed, clear the accept state
      setTimeout(() => {
        console.log('[handleAccept] Clearing accept state after process completes');
        setIsAccepting({});
      }, 300); // Short delay to ensure UI feedback is visible

    } catch (error: any) {
      console.error('API Error:', error);
      let errorMessage: string;
      if (error.response?.data?.detail) {
        errorMessage = typeof error.response.data.detail === 'object'
          ? JSON.stringify(error.response.data.detail)
          : error.response.data.detail;
      } else if (error.message) {
        errorMessage = error.message;
      } else {
        errorMessage = 'An error occurred while accepting the draft';
      }
      setError(errorMessage);
      
      // Show error notification
      setNotification({
        message: 'Failed to accept metadata: ' + errorMessage,
        severity: 'error',
        loading: false
      });
      
      // Clear notification after 5 seconds
      setTimeout(() => setNotification(null), 5000);
    } finally {
      // Clear accepting state after a short delay to ensure UI feedback
      setTimeout(() => {
        setIsAccepting(prev => ({ ...prev, [item.id]: false }));
      }, 500);
    }
  };

  const handleEdit = async (item: MetadataItem) => {
    const itemIndex = items.findIndex((i) => i.id === item.id);
    if (itemIndex !== -1) {
      dispatch({ type: 'SET_CURRENT_INDEX', payload: itemIndex });
      dispatch({ type: 'SET_VIEW_MODE', payload: 'review' });
      try {
        setIsLoadingDetails(true); // Set loading before await
        await loadItemDetails(item.id); // forceRefresh is false by default
        preloadNextItem(itemIndex);
      } catch (error) {
        console.error('Error loading details in handleEdit:', error);
        // setError should be handled within loadItemDetails
      } finally {
        setIsLoadingDetails(false); // Ensure loading is unset
      }
    }
  };

  // Separate handler for editing in review mode
  const handleEditInReview = (item: MetadataItem) => {
    // Ensure we're editing the correct item (table or column) and sync rich-text mode safely
    const inferredType: 'table' | 'column' =
      item.type === 'column' || item.id?.includes('#column.') ? 'column' : 'table';
    const initialRich = Boolean(item.isHtml);
    setIsRichText(initialRich);
    setEditItem({
      ...item,
      type: inferredType, // Preserve or infer the type (table or column)
      id: item.id, // Preserve the full ID which includes column info if it's a column
      draftDescription: initialRich
        ? sanitizeHtml(item.draftDescription || '')
        : (item.draftDescription || ''),
    });
    setEditDialogOpen(true);
  };

  const handleCloseEditDialog = () => {
    setEditDialogOpen(false);
    setEditItem(null);
    setIsRichText(false);
  };

  const handleSaveEdit = async () => {
    if (editItem) {
      try {
        setError(null);

        const isColumn = editItem.type === 'column' || editItem.id.includes('#column.');
        if (isColumn) {
          // Backend (src/backend_apis/main.py) currently provides /update_table_draft_description
          // only and lacks /update_column_draft_description. Fail safely without mutating local state.
          setError(
            'Updating column draft descriptions is not supported by the backend API (/update_column_draft_description is not implemented).'
          );
          return;
        }

        const tableFqn = editItem.id.split('#')[0];
        const [projectId, datasetId, tableId] = tableFqn.split('.');
        const finalDescription = isRichText
          ? sanitizeHtml(editItem.draftDescription || '')
          : (editItem.draftDescription || '');

        await axios.post(
          `${normalizedBaseUrl()}/update_table_draft_description`,
          {
            client_settings: {
              project_id: config.project_id,
              llm_location: config.llm_location,
              dataplex_location: config.dataplex_location,
            },
            table_settings: {
              project_id: projectId,
              dataset_id: datasetId,
              table_id: tableId,
              documentation_uri: editItem.metadata?.external_document_uri || editItem.externalDocumentUri || ""
            },
            description: finalDescription,
            is_html: isRichText,
          },
          getAxiosRequestConfig(apiToken)
        );

        // Update both state and cache with the new sanitized description
        const updatedItem = {
          ...editItem,
          draftDescription: finalDescription,
          isHtml: isRichText,
          lastModified: new Date().toISOString()
        };

        dispatch({
          type: 'UPDATE_ITEM',
          payload: {
            id: editItem.id,
            updates: updatedItem
          }
        });

        // Update cache
        setDetailsCache(prev => ({
          ...prev,
          [editItem.id]: {
            ...prev[editItem.id],
            ...updatedItem
          }
        }));

        handleCloseEditDialog();

        // Refresh the item details to get the updated state
        await loadItemDetails(editItem.id, true);
      } catch (err) {
        setError(extractApiErrorMessage(err, 'Failed to save changes. Please try again.'));
        console.error('Error saving description:', err);
      }
    }
  };

  const handleAddComment = async (itemId: string) => {
    console.log(`handleAddComment triggered for itemId: ${itemId}`);
    if (!newComment.trim()) return; // Prevent empty comments

    try {
      setError(null);
      const isColumn = itemId.includes('#column.');
      let tableFqn: string;
      let projectId: string, datasetId: string, tableIdBase: string;
      let columnName: string | undefined;
      let itemToUpdateInState: MetadataItem | undefined;
      let stateUpdateId: string = itemId; // ID to use for dispatching UPDATE_ITEM

      if (isColumn) {
        console.log('Handling comment for COLUMN');
        const parts = itemId.split('#column.');
        const tablePart = parts[0]; // e.g., project.dataset.table#table
        columnName = parts[1];
        tableFqn = tablePart.replace('#table', ''); // Get FQN: project.dataset.table
        [projectId, datasetId, tableIdBase] = tableFqn.split('.');
        
        // Find the parent table item in state to update its currentColumn if necessary
        itemToUpdateInState = items.find(i => i.id === tablePart);
        if (itemToUpdateInState) {
          stateUpdateId = tablePart; // Update the parent table item in state
          console.log(`Found parent table item in state: ${stateUpdateId}`);
        } else {
           console.warn(`Parent table item ${tablePart} not found in state items for column comment.`);
           // Proceed with API call, but state update might be incomplete
        }

      } else {
        console.log('Handling comment for TABLE');
        // It's a table, find the item directly in the state array
        itemToUpdateInState = items.find(i => i.id === itemId);
        if (!itemToUpdateInState) {
          console.error(`handleAddComment: Table Item with ID ${itemId} not found in state.items. Aborting API call.`);
          setError(`Failed to add comment: Could not find item ${itemId} in the current list.`);
          return;
        }
        tableFqn = itemToUpdateInState.id.replace('#table', '');
        [projectId, datasetId, tableIdBase] = tableFqn.split('.');
        columnName = undefined;
      }

      console.log('handleAddComment: Making API call with:', { projectId, datasetId, tableIdBase, columnName });

      await axios.post(
        `${normalizedBaseUrl()}/metadata/review/add_comment`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          table_settings: {
            project_id: projectId,
            dataset_id: datasetId,
            table_id: tableIdBase, // Use base table ID for API
          },
          column_settings: isColumn ? {
            column_name: columnName
          } : undefined,
          comment: newComment,
          column_name: columnName, // API expects this redundant field
          is_column_comment: isColumn
        },
        getAxiosRequestConfig(apiToken)
      );

      console.log('handleAddComment: API call successful.');

      // --- State and Cache Update --- 
      const newCommentText = newComment; // Store before clearing
      setNewComment(''); // Clear input field immediately

      // 1. Update Cache
      setDetailsCache(prev => {
        const newCache = { ...prev };
        const targetCacheId = itemId; // Use the full ID (table or column)
        
        if (newCache[targetCacheId]) {
          console.log(`Updating comments in cache for ${targetCacheId}`);
          newCache[targetCacheId] = {
            ...newCache[targetCacheId],
            comments: [...(newCache[targetCacheId].comments || []), newCommentText]
          };
        } else {
          console.warn(`Cache entry not found for ${targetCacheId} during comment update.`);
          // Optionally initialize cache entry if needed, though it should exist if viewable
          // newCache[targetCacheId] = { comments: [newCommentText] }; 
        }

        // If it was a column comment, also update the comments array within the cached *table* details
        // This might be redundant if the column details are always fetched fresh, but safer to keep consistent
        if (isColumn && itemToUpdateInState) { // itemToUpdateInState here refers to the parent table
           const parentTableId = itemToUpdateInState.id;
           if (newCache[parentTableId] && newCache[parentTableId].columns) {
             console.log(`Updating comments within parent table cache ${parentTableId} for column ${columnName}`);
             newCache[parentTableId] = {
               ...newCache[parentTableId],
               columns: newCache[parentTableId].columns.map((col: any) => {
                 if (col.name.endsWith(`.${columnName}`)) {
                   return {
                     ...col,
                     comments: [...(col.comments || []), newCommentText]
                   };
                 }
                 return col;
               })
             };
           }
        }
        return newCache;
      });

      // 2. Update State (via dispatch)
      if (itemToUpdateInState) {
          console.log(`Updating item in state: ${stateUpdateId}`);
          let updates: Partial<MetadataItem>;

      if (isColumn) {
            // Update the currentColumn within the parent table item state
            if (itemToUpdateInState.currentColumn && itemToUpdateInState.currentColumn.id === itemId) {
               updates = {
                 currentColumn: {
                   ...itemToUpdateInState.currentColumn,
                   comments: [...(itemToUpdateInState.currentColumn.comments || []), newCommentText]
                 }
               };
            } else {
               console.warn('State update: Current column in state does not match commented column. Skipping state update for currentColumn.');
               return; // Don't dispatch if state seems inconsistent
            }
          } else {
            // Update the comments directly on the table item state
            updates = {
              comments: [...(itemToUpdateInState.comments || []), newCommentText]
            };
          }
          
          dispatch({
            type: 'UPDATE_ITEM',
            payload: { 
              id: stateUpdateId, // Use table ID if it was a column comment
              updates
            }
          });
          console.log('handleAddComment: State dispatch complete.');
      } else {
           console.warn('handleAddComment: itemToUpdateInState was not found, cannot dispatch state update.');
      }

    } catch (error: any) {
      console.error('Error adding comment:', error);
      setError(error.response?.data?.detail || 'Failed to add comment');
    }
  };

  const handleMarkForRegeneration = async (itemId: string) => {
    try {
      console.log('Marking for regeneration:', itemId);
      
      // Store current navigation state to preserve it after background process
      const navigationState = {
        currentlyDisplayedItemId: isColumnView ? 
          (currentItemId && currentColumnIndex >= 0 ? 
            `${currentItemId}#column.${taggedColumns[currentColumnIndex]?.name?.split('.').pop()}` : 
            currentItemId) : 
          currentItemId,
        isViewingColumn: isColumnView,
        currentColumnIndex: currentColumnIndex,
        tableId: currentItemId // The parent table ID
      };
      
      console.log('Saved navigation state before regeneration:', navigationState);
      
      // For columns, we need to handle the case where the column might not be in the items array
      let item: MetadataItem | undefined;
      let isColumn = false;
      let columnName: string | undefined;
      let tableFqn: string;
      let tableId: string;
      
      if (itemId.includes('#column.')) {
        isColumn = true;
        // Extract the table FQN and column name
        const parts = itemId.split('#column.');
        // Remove #table suffix if present
        tableFqn = parts[0].replace('#table', '');
        tableId = parts[0]; // Keep the original table ID with #table suffix
        columnName = parts[1];
        
        console.log('Extracted column info:', { tableFqn, columnName, tableId });
        
        // Find the parent table item
        const tableItem = items.find(i => i.id === tableId);
        if (!tableItem) {
          throw new Error('Parent table not found');
        }
        
        // For columns, we might need to construct a temporary item
        if (tableItem.currentColumn && tableItem.currentColumn.id === itemId) {
          // Use the current column if it matches
          item = {
            ...tableItem.currentColumn,
            id: itemId,
            type: 'column'
          };
        } else {
          // Try to find the column in the table's tagged columns
          const column = taggedColumns.find(c => c.name.endsWith(`.${columnName}`));
          if (column) {
            item = {
              ...column,
              id: itemId,
              type: 'column',
              status: 'draft',
              lastModified: new Date().toISOString()
            };
          }
        }
        
        // If we still don't have an item, create a minimal one
        if (!item) {
          item = {
            id: itemId,
            type: 'column',
            name: columnName,
            isMarkingForRegeneration: false,
            status: 'draft',
            lastModified: new Date().toISOString()
          };
        }
        
        // Set loading state for the column
        // First, update the column's loading state
        dispatch({
          type: 'UPDATE_ITEM',
          payload: { id: itemId, updates: { isMarkingForRegeneration: true } }
        });
        
        // Then, update the column within the parent table
        if (tableItem.currentColumn) {
          const updatedTable = {
            ...tableItem,
            currentColumn: {
              ...tableItem.currentColumn,
              isMarkingForRegeneration: true
            }
          };
          
          dispatch({
            type: 'UPDATE_ITEM',
            payload: { id: tableId, updates: updatedTable }
          });
        }
      } else {
        // For tables, use the existing logic but remove #table suffix if present
        tableFqn = itemId.replace('#table', '');
        tableId = itemId;
        console.log('Extracted table FQN:', tableFqn);
        
        item = items.find(i => i.id === itemId);
        if (!item) throw new Error('Item not found');
        
        // Set loading state for the table
        dispatch({
          type: 'UPDATE_ITEM',
          payload: { id: itemId, updates: { isMarkingForRegeneration: true } }
        });
      }

      // Make the API call
      console.log('Making API call with:', { tableFqn, columnName });
      const response = await axios.post(
        `${normalizedBaseUrl()}/mark_for_regeneration`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          request: {
            table_fqn: tableFqn,
            column_name: columnName
          },
          column_settings: isColumn ? {
            column_name: columnName
          } : undefined
        },
        getAxiosRequestConfig(apiToken)
      );

      console.log('Mark for regeneration response:', response.data);
      
      // Debug log the component state to help with troubleshooting
      console.log('[handleMarkForRegeneration] Component state after API call:', {
        currentItemId,
        isColumnView,
        currentColumnIndex,
        taggedColumnsLength: taggedColumns.length,
        currentColumnName: currentColumnIndex >= 0 ? taggedColumns[currentColumnIndex]?.name : null
      });
      
      // Get the current displayed item ID based on component state
      const currentDisplayedItemId = isColumnView ? 
        (currentItemId && currentColumnIndex >= 0 ? 
          `${currentItemId}#column.${taggedColumns[currentColumnIndex]?.name?.split('.').pop()}` : 
          currentItemId) : 
        currentItemId;
      
      // Verify if the user is still viewing the same item
      const userNavigatedAway = navigationState.currentlyDisplayedItemId !== currentDisplayedItemId;
      console.log('Navigation state check:', { 
        originalViewedItem: navigationState.currentlyDisplayedItemId,
        currentViewedItem: currentDisplayedItemId,
        userNavigatedAway 
      });

      // Clear the cache for this item to ensure we get fresh data next time it's loaded
      // but don't trigger a refresh now (it takes too long)
      // ONLY update the cache if the user hasn't navigated away
      if (!userNavigatedAway) {
        // Instead of clearing the cache entry, update it in place
        setDetailsCache(prev => {
          const existingEntry = prev[itemId];
          if (!existingEntry) {
            // If no existing entry, nothing to update
            return prev;
          }
          
          // Update the cache with the new status but keep all other content
          return {
            ...prev,
            [itemId]: {
              ...existingEntry,
              markedForRegeneration: true,
              isMarkingForRegeneration: false
            }
          };
        });
      } else {
        console.log('[handleMarkForRegeneration] User navigated away, skipping cache clear to prevent content replacement');
      }

      // For tables, just reset the table
      dispatch({
        type: 'UPDATE_ITEM',
        payload: { id: itemId, updates: { isMarkingForRegeneration: false } }
      });
    } catch (error: any) {
      // Add proper error handling here
      setError(error.message || 'Failed to mark for regeneration');
    }
  };

  const handleNext = async () => {
    if (currentItemIndex < items.length - 1) {
      const nextIndex = currentItemIndex + 1;
      const nextItem = items[nextIndex]; // This is the *next* table item

      // --- START: Reset column state before navigating table ---
      console.log('[handleNext] START - Resetting column state. Current state:', { currentColumnIndex, isColumnDataReady });
      setIsColumnDataReady(false); // <-- Mark column data as not ready
      setCurrentColumnIndex(-1);
      // REMOVED: setTaggedColumns([]); // Let loadItemDetails handle setting columns for the new table
      // Ensure the main item ID reflects the upcoming table
      setCurrentItemId(nextItem.id); // Use the actual next item's ID
      // Ensure the Redux state's currentColumn is cleared for the item we are navigating TO
      dispatch({
         type: 'UPDATE_ITEM',
         payload: { id: nextItem.id, updates: { currentColumn: null } }
      });
       // Also ensure the *previous* item in Redux doesn't retain a currentColumn
       const previousItem = items[currentItemIndex];
       if (previousItem?.id) { // Check if previous item exists
          dispatch({
             type: 'UPDATE_ITEM',
             payload: { id: previousItem.id, updates: { currentColumn: null } }
          });
       }
      console.log('[handleNext] Reset state:', { currentColumnIndex: -1, isColumnDataReady: false });
      // --- END: Reset column state ---

      // Now update the main index in Redux state
      dispatch({ type: 'SET_CURRENT_INDEX', payload: nextIndex });
      // setCurrentItemId local state is already updated above

      try {
        setIsLoadingDetails(true); // Use the main details loader
        // Load details for the *new* table
        await loadItemDetails(nextItem.id); // forceRefresh is false by default
        preloadNextItem(nextIndex);
      } catch (error) {
        console.error('Error in handleNext:', error);
        // setError handled in loadItemDetails
      } finally {
        setIsLoadingDetails(false);
      }
    }
  };

  const handlePrevious = async () => {
    if (currentItemIndex > 0) {
      const prevIndex = currentItemIndex - 1;
      const prevItem = items[prevIndex]; // This is the *previous* table item

      // --- START: Reset column state before navigating table ---
       console.log('[handlePrevious] START - Resetting column state. Current state:', { currentColumnIndex, isColumnDataReady });
       setIsColumnDataReady(false); // <-- Mark column data as not ready
       setCurrentColumnIndex(-1);
       // REMOVED: setTaggedColumns([]); // Let loadItemDetails handle setting columns for the new table
       // Ensure the main item ID reflects the upcoming table
       setCurrentItemId(prevItem.id); // Use the actual prev item's ID
        // Ensure the Redux state's currentColumn is cleared for the item we are navigating TO
       dispatch({
          type: 'UPDATE_ITEM',
          payload: { id: prevItem.id, updates: { currentColumn: null } }
       });
        // Also ensure the *current* item in Redux (the one we are leaving) doesn't retain a currentColumn
        const currentItem = items[currentItemIndex];
        if (currentItem?.id) { // Check if current item exists
           dispatch({
              type: 'UPDATE_ITEM',
              payload: { id: currentItem.id, updates: { currentColumn: null } }
           });
        }
       console.log('[handlePrevious] Reset state:', { currentColumnIndex: -1, isColumnDataReady: false });
      // --- END: Reset column state ---

      // Now update the main index in Redux state
      dispatch({ type: 'SET_CURRENT_INDEX', payload: prevIndex });
       // setCurrentItemId local state is already updated above

      try {
        setIsLoadingDetails(true); // Use the main details loader
        // Load details for the *new* table (which is the previous item)
        await loadItemDetails(prevItem.id); // forceRefresh is false by default
        preloadNextItem(prevIndex); // Preload based on the new index
      } catch (error) {
        console.error('Error in handlePrevious:', error);
        // setError handled in loadItemDetails
      } finally {
        setIsLoadingDetails(false);
      }
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'draft':
        return 'warning';
      case 'accepted':
        return 'success';
      case 'rejected':
        return 'error';
      default:
        return 'default';
    }
  };

  const truncateHtml = (html: string, maxLength: number = 150) => {
    const text = stripHtmlToText(html);

    if (text.length <= maxLength) {
      return sanitizeHtml(html);
    }

    const truncated = text.slice(0, maxLength) + '...';
    return `<p>${escapeHtmlText(truncated)}</p>`;
  };

  const renderDescription = (description: string, isHtml: boolean, truncate: boolean = false) => {
    if (!description) return null;

    if (!isHtml) {
      const displayText =
        truncate && description.length > 150
          ? `${description.slice(0, 150)}...`
          : description;
      return (
        <Box
          sx={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            ...(truncate && {
              maxHeight: '100px',
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
            })
          }}
        >
          <Typography>{displayText}</Typography>
        </Box>
      );
    }

    const safeContent = sanitizeHtml(truncate ? truncateHtml(description) : description);

    return (
      <Box 
        dangerouslySetInnerHTML={{ __html: safeContent }}
        sx={{ 
          '& p': { margin: '0.5em 0' },
          '& ul, & ol': { marginLeft: '1.5em' },
          '& a': { color: 'primary.main' },
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          ...(truncate && {
            maxHeight: '100px',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
          })
        }}
      />
    );
  };

  const renderDescriptionWithDiff = (currentDescription: string, draftDescription: string, isHtml: boolean) => {
    const oldText = isHtml ? stripHtmlToText(currentDescription || '') : currentDescription || '';
    const newText = isHtml ? stripHtmlToText(draftDescription || '') : draftDescription || '';

    // If there are no changes, show a message indicating this
    const hasChanges = oldText !== newText;

    return (
      <Box>
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={showDiff}
                  onChange={(e) => setShowDiff(e.target.checked)}
                />
              }
              label={showDiff ? "Showing Diff View" : "Showing Side by Side"}
            />
            {!hasChanges && (
              <Typography variant="subtitle1" color="text.secondary">
                No changes detected between current and draft versions
              </Typography>
            )}
          </Box>
          {hasChanges && (
            <Alert severity="info" sx={{ mb: 2 }}>
              This item has draft changes that need review
            </Alert>
          )}
        </Box>
        
        {showDiff ? (
          <Box sx={{ 
            minHeight: '400px',
            '& .diff-viewer': {
              width: '100%',
              minHeight: '400px'
            },
            '& .diff-container': {
              margin: 0,
              padding: '16px'
            },
            '& .diff-title-header': {
              padding: '8px 16px',
              background: '#f5f5f5',
            }
          }}>
            <ReactDiffViewer
              oldValue={oldText}
              newValue={newText}
              splitView={true}
              leftTitle="Current Version"
              rightTitle="Draft Version"
              compareMethod={DiffMethod.WORDS}
              styles={{
                variables: {
                  light: {
                    diffViewerBackground: '#f8f9fa',
                    diffViewerColor: '#212529',
                    addedBackground: '#e6ffec',
                    addedColor: '#24292f',
                    removedBackground: '#ffebe9',
                    removedColor: '#24292f',
                    wordAddedBackground: '#abf2bc',
                    wordRemovedBackground: '#fdb8c0',
                  }
                }
              }}
            />
          </Box>
        ) : (
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <Typography variant="h6" gutterBottom>Current Version</Typography>
              <Paper sx={{ p: 2, minHeight: '200px' }}>
                {isHtml ? (
                  <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(currentDescription || 'No description available') }} />
                ) : (
                  <Typography>{currentDescription || 'No description available'}</Typography>
                )}
              </Paper>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="h6" gutterBottom>Draft Version</Typography>
              <Paper sx={{ p: 2, minHeight: '200px', bgcolor: hasChanges ? 'rgba(200, 250, 205, 0.15)' : undefined }}>
                {isHtml ? (
                  <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(draftDescription || 'No draft description available') }} />
                ) : (
                  <Typography>{draftDescription || 'No draft description available'}</Typography>
                )}
              </Paper>
            </Grid>
          </Grid>
        )}
      </Box>
    );
  };

  const renderComments = (comments: string[] = []) => (
    <List>
      {comments.map((comment, index) => (
        <React.Fragment key={index}>
          <ListItem>
            <ListItemText primary={comment} />
          </ListItem>
          {index < comments.length - 1 && <Divider />}
        </React.Fragment>
      ))}
    </List>
  );

  const renderListMode = () => (
    <Box ref={listContainerRef} sx={{ maxHeight: 'calc(100vh - 180px)', overflow: 'auto' }}>
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Tags</TableCell>
              <TableCell>Tagged Columns</TableCell>
              <TableCell>Regeneration</TableCell>
              <TableCell>Last Modified</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.map((item) => {
              const itemDetails = detailsCache[item.id];
              const taggedColumnsCount = itemDetails?.columns?.filter((col: any) => 
                col.tags && Object.keys(col.tags).length > 0 && 
                col.tags.some((tag: any) => tag.description && tag.description.trim() !== '')
              )?.length || 0;

              return (
                <TableRow
                  key={item.id}
                  onClick={() => handleViewDetails(item.id)}
                  sx={{ 
                    '&:last-child td, &:last-child th': { border: 0 },
                    '&:hover': {
                      backgroundColor: 'action.hover',
                      cursor: 'pointer'
                    }
                  }}
                >
                  <TableCell component="th" scope="row">
                    {item.name}
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={item.type.charAt(0).toUpperCase() + item.type.slice(1)}
                      size="small"
                      color={item.type === 'table' ? 'primary' : 'secondary'}
                    />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={item.status.charAt(0).toUpperCase() + item.status.slice(1)}
                      size="small"
                      color={getStatusColor(item.status)}
                    />
                  </TableCell>
                  <TableCell>
                    {item.tags && (
                      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                        {Object.entries(item.tags).map(([tag, value]) => (
                          <Chip
                            key={tag}
                            label={tag}
                            size="small"
                            variant="outlined"
                          />
                        ))}
                      </Box>
                    )}
                  </TableCell>
                  <TableCell>
                    {item.type === 'table' && taggedColumnsCount > 0 && (
                      <Chip
                        label={`${taggedColumnsCount} tagged columns`}
                        size="small"
                        color="info"
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', alignItems: 'center' }}>
                      <IconButton
                        onClick={(e) => {
                          e.stopPropagation();
                          handleMarkForRegeneration(item.id);
                        }}
                        size="small"
                        disabled={(() => {
                          const isDisabled = item.markedForRegeneration || item.isMarkingForRegeneration;
                          console.log('Regeneration Button Debug:', {
                            itemId: item.id,
                            markedForRegeneration: item.markedForRegeneration,
                            isMarkingForRegeneration: item.isMarkingForRegeneration,
                            isDisabled,
                            currentItem: {
                              id: item.id,
                              type: item.type,
                              name: item.name,
                              status: item.status
                            }
                          });
                          return isDisabled;
                        })()}
                      >
                        <AutorenewIcon sx={{ 
                          animation: item.isMarkingForRegeneration ? 'spin 1s linear infinite' : 'none',
                          '@keyframes spin': {
                            '0%': { transform: 'rotate(0deg)' },
                            '100%': { transform: 'rotate(360deg)' }
                          }
                        }} />
                      </IconButton>
                    </Box>
                  </TableCell>
                  <TableCell>{item.lastModified}</TableCell>
                  <TableCell align="right">
                    <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1 }}>
                      <IconButton
                        onClick={(e) => {
                          e.stopPropagation();
                          handleViewDetails(item.id);
                        }}
                        size="small"
                      >
                        <VisibilityIcon />
                      </IconButton>
                    </Box>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );

  const renderActionBar = (currentItem: MetadataItem) => {
    // Determine if we're viewing a column more accurately
    const isViewingColumn = Boolean(currentItem.currentColumn) || currentItem.type === 'column';
    const isViewingTable = !isViewingColumn;
    
    // Check if the current table has tagged columns
    const hasTaggedColumns = taggedColumns.length > 0;
    
    // Debug information for regeneration button
    console.group('Regeneration Button Debug - Review Mode');
    console.log('Current Item:', {
      id: currentItem.id,
      type: currentItem.type,
      name: currentItem.name,
      markedForRegeneration: currentItem.markedForRegeneration,
      isMarkingForRegeneration: currentItem.isMarkingForRegeneration,
      currentColumn: currentItem.currentColumn ? {
        id: currentItem.currentColumn.id,
        name: currentItem.currentColumn.name,
        markedForRegeneration: currentItem.currentColumn.markedForRegeneration,
        isMarkingForRegeneration: currentItem.currentColumn.isMarkingForRegeneration
      } : null
    });
    
    // Calculate disabled state based on whether we're viewing a table or a column
    let isDisabled;
    
    // Determine the effective type for the current view
    const effectiveType = isViewingColumn ? 'column' : 'table';
    
    if (currentItem.type === 'column') {
      // For columns, only disable if this specific column is marked or marking
      isDisabled = currentItem.markedForRegeneration || currentItem.isMarkingForRegeneration;
    } else if (currentItem.currentColumn) {
      // For columns viewed within a table, use the column's status
      isDisabled = currentItem.currentColumn.markedForRegeneration || currentItem.currentColumn.isMarkingForRegeneration;
    } else {
      // For tables, disable if the table is marked or marking
      isDisabled = currentItem.markedForRegeneration || currentItem.isMarkingForRegeneration;
    }
    
    console.log('Button State:', {
      isDisabled,
      type: effectiveType, // Use the effective type instead of currentItem.type
      hasCurrentColumn: Boolean(currentItem.currentColumn),
      markedForRegeneration: currentItem.markedForRegeneration,
      isMarkingForRegeneration: currentItem.isMarkingForRegeneration,
      columnMarkedForRegeneration: currentItem.currentColumn?.markedForRegeneration,
      columnIsMarkingForRegeneration: currentItem.currentColumn?.isMarkingForRegeneration
    });
    console.groupEnd();
    
    // --- Determine effective item and acceptance status for Action Bar --- START
    const effectiveItem: MetadataItem = (currentItem.currentColumn as MetadataItem) ?? currentItem;
    const isEffectivelyAccepted = effectiveItem.isAccepted;
    const interactiveItemId = effectiveItem.id; 
    // --- Determine effective item and acceptance status for Action Bar --- END
    
    // --- Add logging for Accept button state --- START
    console.log('[renderActionBar] Accept Button State Debug:', {
         effectiveItemId: effectiveItem.id,
         isEffectivelyAccepted: isEffectivelyAccepted, // Should be true if accepted
         hasDraft: Boolean(effectiveItem.draftDescription), // Should ideally be irrelevant if accepted
         isAcceptingNow: isAccepting[interactiveItemId] // Should be false unless clicked
    });
    // --- Add logging for Accept button state --- END

    // +++ START: Add detailed logging for button states +++
    console.log('[renderActionBar] Button State Check:', {
      currentItemId: currentItem.id,
      isViewingColumn,
      hasTaggedColumns,
      taggedColumnsLength: taggedColumns.length,
      currentColumnIndex,
      isColumnDataReady,
      // Button Disabled States Calculation:
      prevColDisabled: !isColumnDataReady || !hasTaggedColumns || currentColumnIndex <= 0,
      tableBtnDisabled: !isViewingColumn,
      nextColDisabled: !isColumnDataReady || !hasTaggedColumns || currentColumnIndex >= taggedColumns.length - 1
    });
    // +++ END: Add detailed logging for button states +++

    return (
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {/* Left side - Edit and Accept buttons */}
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Button
            variant="contained"
            color="primary"
            onClick={() => handleEditInReview(effectiveItem)} // Pass the effective item
            startIcon={<EditIcon />}
          >
            Edit
          </Button>
          <Button
            variant="contained"
            color="success"
            onClick={() => handleAccept(effectiveItem)} // Pass the effective item
            startIcon={<CheckCircleIcon />}
            disabled={!effectiveItem.draftDescription || isAccepting[interactiveItemId] || isEffectivelyAccepted} // Use effective status
          >
            {isAccepting[interactiveItemId] ? 'Accepting...' : (isEffectivelyAccepted ? 'Accepted' : 'Accept')} 
          </Button>
          <Button
            variant="outlined"
            color="warning"
            onClick={() => {
              // When viewing a column within a table, use the column's ID
              if (currentItem.currentColumn) {
                handleMarkForRegeneration(currentItem.currentColumn.id);
              } else {
                handleMarkForRegeneration(currentItem.id);
              }
            }}
            startIcon={
              <AutorenewIcon sx={{ 
                animation: currentItem.isMarkingForRegeneration || 
                           (currentItem.currentColumn && currentItem.currentColumn.isMarkingForRegeneration) 
                           ? 'spin 1s linear infinite' : 'none',
                '@keyframes spin': {
                  '0%': { transform: 'rotate(0deg)' },
                  '100%': { transform: 'rotate(360deg)' }
                }
              }} />
            }
            disabled={isDisabled}
          >
            {currentItem.isMarkingForRegeneration || 
             (currentItem.currentColumn && currentItem.currentColumn.isMarkingForRegeneration) 
             ? 'Marking...' : 'Mark for Regeneration'}
          </Button>
          {/* Debug button - kept for development purposes */}
          <Button
            variant="outlined"
            color="info"
            onClick={logDebugInfo}
            size="small"
            sx={{ ml: 1 }}
          >
            Debug
          </Button>
        </Box>

        {/* Right side - Navigation buttons */}
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
          {/* Previous button */}
          <Button
            variant="outlined"
            onClick={handlePrevious}
            disabled={currentItemIndex === 0}
            startIcon={<NavigateBeforeIcon />}
          >
            Previous
          </Button>

          {/* Column navigation buttons */}
          <Box sx={{ display: 'flex', gap: 1, mx: 2 }}>
            <Button
              variant={hasTaggedColumns ? "contained" : "outlined"}
              color="info"
              onClick={handlePrevColumn}
              disabled={!isColumnDataReady || !hasTaggedColumns || currentColumnIndex <= 0}
              startIcon={<NavigateBeforeIcon />}
              sx={{ minWidth: '120px' }}
            >
              Prev Column
            </Button>
            <Button
              variant={isViewingColumn ? "contained" : "outlined"}
              color="info"
              onClick={handleBackToTable}
              disabled={!isViewingColumn}
              sx={{ minWidth: '80px' }}
            >
              Table
            </Button>
            <Button
              variant={hasTaggedColumns ? "contained" : "outlined"}
              color="info"
              onClick={handleNextColumn}
              disabled={!isColumnDataReady || !hasTaggedColumns || currentColumnIndex >= taggedColumns.length - 1}
              endIcon={<NavigateNextIcon />}
              sx={{ minWidth: '120px' }}
            >
              Next Column
            </Button>
          </Box>

          {/* Next button */}
          <Button
            variant="outlined"
            onClick={handleNext}
            disabled={currentItemIndex === items.length - 1}
            endIcon={<NavigateNextIcon />}
          >
            Next
          </Button>
        </Box>
      </Box>
    );
  };

  // Helper function to process cached column data (similar to logic added before)
  const processCachedColumnData = (baseItem: MetadataItem, cachedData: any): MetadataItem => {
      if (!cachedData) return baseItem; // Return base if no cache

      const columnMetadata = cachedData.metadata || {};
      const acceptedStatus = columnMetadata['is-accepted'];
      const acceptedTimestamp = columnMetadata['when-accepted'];
      const isItemAccepted = acceptedStatus === true || String(acceptedStatus).toLowerCase() === 'true';

      return {
          ...baseItem, // Start with base constructed item
          ...cachedData, // Overwrite with raw cached data
          // Explicitly set derived fields:
          metadata: { // Ensure metadata includes derived status
               ...columnMetadata,
              'is-accepted': isItemAccepted,
              'when-accepted': acceptedTimestamp,
          },
          isAccepted: isItemAccepted,
          whenAccepted: acceptedTimestamp,
          status: isItemAccepted ? 'accepted' : (cachedData.status || 'draft'),
          // Ensure other necessary fields from MetadataItem interface are present
          currentDescription: cachedData.currentDescription || baseItem.currentDescription || '',
          draftDescription: cachedData.draftDescription || baseItem.draftDescription || '',
          comments: cachedData.comments || baseItem.comments || [],
          markedForRegeneration: cachedData.markedForRegeneration || baseItem.markedForRegeneration || false,
          isMarkingForRegeneration: cachedData.isMarkingForRegeneration || baseItem.isMarkingForRegeneration || false,
      };
  };

  const handleNextColumn = () => {
    // Instead of blocking navigation, just log that there's a process running
    if (Object.values(isAccepting).some(value => value === true)) {
      console.log('[handleNextColumn] Navigation during background accept process - this is allowed but may cause visual effects');
      // Continue with navigation - don't return
    }

    const tableId = currentItemId;
    if (!tableId) {
      console.error('No current table to navigate columns for');
      return;
    }

    // Debug
    console.log('[handleNextColumn] START - State Check:', {
      tableId,
      currentColumnIndex,
      numberOfColumns: taggedColumns.length,
      isColumnDataReady
    });

    // Wait until column data is ready before allowing navigation
    if (!isColumnDataReady) {
      console.log('[handleNextColumn] Columns not ready yet, cancelling navigation');
      return;
    }

    // Check if we have columns to navigate
    if (taggedColumns.length === 0) {
      console.log('[handleNextColumn] No columns to navigate');
      return;
    }

    // Calculate the target index
    const nextIndex = currentColumnIndex >= taggedColumns.length - 1 ? 0 : currentColumnIndex + 1;
    const columnName = nextIndex >= 0 && nextIndex < taggedColumns.length ? 
      taggedColumns[nextIndex].name.split('.').pop() : null;
      
    if (!columnName) {
      console.error('[handleNextColumn] Could not determine column name for index:', nextIndex);
      return;
    }

    const targetColumnId = `${tableId}#column.${columnName}`;
    console.log('[handleNextColumn] Navigating to next column:', targetColumnId, 'index:', nextIndex);

    // Update our state
    setCurrentColumnIndex(nextIndex);
    console.log('[handleNextColumn] Updated local currentColumnIndex:', nextIndex);

    // Ensure the column we are navigating to will be displayed
    if (items.length > 0) {
      const currentTableItem = items.find(i => i.id === tableId);
      if (currentTableItem) {
        // Get the column data
        const column = taggedColumns[nextIndex];
        if (column) {
          // Build a column item to set as the currentColumn of the table
          const columnItem = {
            ...column,
            id: targetColumnId || `${tableId}#column.${column.name.split('.').pop()}`, // Ensure id is not undefined
            type: 'column' as const
          };

          // Update parent table to show this column directly WITHOUT waiting for loadItemDetails
          // This ensures immediate response to navigation
          dispatch({
            type: 'UPDATE_ITEM',
            payload: {
              id: tableId,
              updates: { currentColumn: columnItem }
            }
          });
          
          // Prevent loadItemDetails from replacing the UI during background loading
          hasManuallySetColumnFlag.current = true;
          
          // After brief delay, clear the flag to allow other operations (just safety)
          setTimeout(() => {
            if (!Object.values(isAccepting).some(value => value === true)) {
              // Only clear if no accept process is running
              hasManuallySetColumnFlag.current = false;
            } else {
              console.log('[Navigation] Accept process is still running, keeping navigation flag active');
            }
          }, 1000);
        }
      }
    }

    // Trigger load of the column details in the background
    console.log('[handleNextColumn] Triggering loadItemDetails for target column:', targetColumnId);
    loadItemDetails(targetColumnId);
  };

  const handlePrevColumn = () => {
    // Instead of blocking navigation, just log that there's a process running
    if (Object.values(isAccepting).some(value => value === true)) {
      console.log('[handlePrevColumn] Navigation during background accept process - this is allowed but may cause visual effects');
      // Continue with navigation - don't return
    }
    
    const tableId = currentItemId;
    if (!tableId) {
      console.error('No current table to navigate columns for');
      return;
    }

    // Debug
    console.log('[handlePrevColumn] START - State Check:', {
      tableId,
      currentColumnIndex,
      numberOfColumns: taggedColumns.length,
      isColumnDataReady
    });

    // Wait until column data is ready before allowing navigation
    if (!isColumnDataReady) {
      console.log('[handlePrevColumn] Columns not ready yet, cancelling navigation');
      return;
    }

    // Calculate previous index, with wraparound
    const prevIndex = currentColumnIndex <= 0 ? taggedColumns.length - 1 : currentColumnIndex - 1;
    const columnName = prevIndex >= 0 && prevIndex < taggedColumns.length ? 
      taggedColumns[prevIndex].name.split('.').pop() : null;
      
    if (!columnName) {
      console.error('[handlePrevColumn] Could not determine column name for index:', prevIndex);
      return;
    }

    const targetColumnId = `${tableId}#column.${columnName}`;
    console.log('[handlePrevColumn] Navigating to previous column:', targetColumnId, 'index:', prevIndex);

    // Update our state first to reflect the target column
    setCurrentColumnIndex(prevIndex);
    console.log('[handlePrevColumn] Updated local currentColumnIndex:', prevIndex);

    // Ensure the column we are navigating to will be displayed immediately without waiting for the API
    if (items.length > 0) {
      const currentTableItem = items.find(i => i.id === tableId);
      if (currentTableItem) {
        // Get the column data
        const column = taggedColumns[prevIndex];
        if (column) {
          // Build a column item to set as the currentColumn of the table
          const columnItem = {
            ...column,
            id: targetColumnId || `${tableId}#column.${column.name.split('.').pop()}`, // Ensure id is not undefined
            type: 'column' as const
          };

          // Update parent table to show this column directly WITHOUT waiting for loadItemDetails
          // This ensures immediate response to navigation
          dispatch({
            type: 'UPDATE_ITEM',
            payload: {
              id: tableId,
              updates: { currentColumn: columnItem }
            }
          });
          
          // Prevent loadItemDetails from replacing the UI during background loading
          hasManuallySetColumnFlag.current = true;
          
          // After brief delay, clear the flag to allow other operations (just safety)
          setTimeout(() => {
            if (!Object.values(isAccepting).some(value => value === true)) {
              // Only clear if no accept process is running
              hasManuallySetColumnFlag.current = false;
            } else {
              console.log('[Navigation] Accept process is still running, keeping navigation flag active');
            }
          }, 1000);
        }
      }
    }

    // Trigger load of the column details in the background
    console.log('[handlePrevColumn] Triggering loadItemDetails for target column:', targetColumnId);
    loadItemDetails(targetColumnId);
  };

  const handleBackToTable = async () => {
    const currentItem = items[currentItemIndex];
    if (!currentItem) {
      console.log('[handleBackToTable] Cannot go back to table: no current item');
      return;
    }

    if (!currentItem.currentColumn) {
      console.log('[handleBackToTable] Already in table view');
      return;
    }

    // Get the table ID from the current item
    const tableId = currentItem.id;

    console.log('[handleBackToTable] START - Going back to table:', tableId, 'from column:', currentItem.currentColumn.id);
    console.log('[handleBackToTable] State before reset:', { currentColumnIndex });

    try {
      // Set loading state to provide visual feedback
      setIsLoadingDetails(true);
    
      // Reset column index
      setCurrentColumnIndex(-1);
    
      // Ensure currentItemId is set to the table ID
      setCurrentItemId(tableId);
      
      // Create a clean copy of the current item without the column data
      const tableItem = {
        ...currentItem,
        currentColumn: null
      };
      
      // Update the state to show the table instead of the column
    dispatch({
      type: 'UPDATE_ITEM',
      payload: {
        id: tableId,
          updates: tableItem
        }
      });
      
      // Log for debugging
      console.log('Back to table view complete - updated state:', {
        tableId,
        hasCurrentColumn: Boolean(tableItem.currentColumn),
        taggedColumnsCount: taggedColumns.length, // Log taggedColumns length *after* potential state update
        currentColumnIndex: -1 // Explicitly log the intended index state
      });
    } catch (error) {
      console.error('[handleBackToTable] Error going back to table:', error);
      setError('Failed to go back to table view. Please try again.');
    } finally {
      setIsLoadingDetails(false);
    }
  };

  const loadItemDetails = async (itemId: string, forceRefresh = false): Promise<MetadataItem | null> => {
    // Determine if loading a column or table
    const isColumn = itemId.includes('#column.');
    const currentTableId = isColumn ? itemId.split('#column.')[0] : itemId; // Get the base table ID

    // Use details loader only for explicit refresh or initial load, not preloads/cache hits
    const showLoader = forceRefresh || !detailsCache[itemId]; 
    if (showLoader) {
        setIsLoadingDetails(true);
    }

    try {
      setError(null);
      console.log('[loadItemDetails] Starting for:', itemId, 'Force refresh:', forceRefresh);

      // --- Cache Check ---
      if (!forceRefresh && detailsCache[itemId]) {
        console.log('[loadItemDetails] Using cached details for:', itemId);
        const cachedDetails = detailsCache[itemId];
        let isCacheValid = true; // Flag to track cache validity

        // Directly use cached column details if available
        if (isColumn) {
            // Check if cached column data is valid
            if (!cachedDetails || !cachedDetails.id || cachedDetails.type !== 'column') {
                console.warn(`[loadItemDetails - Cache Hit - Column] Stale or invalid cache for ${itemId}. Forcing API refresh.`);
                isCacheValid = false; // Mark cache as invalid
            } else {
                // +++ START: Explicitly process cached column details +++
                console.log('[loadItemDetails - Cache Hit - Column] Processing cached details for:', itemId);
                
                // Derive status similar to API path
                const columnMetadata = cachedDetails.metadata || {};
                const acceptedStatus = columnMetadata['is-accepted'];
                const acceptedTimestamp = columnMetadata['when-accepted'];
                // Check cache values directly as well, prioritizing them
                const isItemAccepted = cachedDetails.isAccepted === true || acceptedStatus === true || String(acceptedStatus).toLowerCase() === 'true';
                const finalStatus = isItemAccepted ? 'accepted' : (cachedDetails.status || 'draft');
                const finalTimestamp = cachedDetails.whenAccepted || acceptedTimestamp;

                const processedCachedColumn: MetadataItem = {
                    ...cachedDetails, // Start with cached data
                    id: itemId, // <<< Explicitly use the correct input itemId
                    isAccepted: isItemAccepted, // Ensure derived value is set
                    status: finalStatus,      // Ensure derived value is set
                    whenAccepted: finalTimestamp, // Ensure derived value is set
                    // Ensure metadata reflects derived status too (optional but good practice)
                    metadata: {
                        ...columnMetadata,
                        'is-accepted': isItemAccepted,
                        'when-accepted': finalTimestamp
                    }
                };

                console.log('[loadItemDetails - Cache Hit - Column] PRE-DISPATCH check for Table ID:', currentTableId);
                console.log('[loadItemDetails - Cache Hit - Column] PRE-DISPATCH currentColumn payload:', processedCachedColumn);

                dispatch({
                    type: 'UPDATE_ITEM',
                    payload: {
                        id: currentTableId, // Update the parent table...
                        updates: { currentColumn: processedCachedColumn } // ...with the PROCESSED cached details
                    }
                });
                 // Also ensure the standalone column entry in state is up-to-date if needed elsewhere
                dispatch({ type: 'UPDATE_ITEM', payload: { id: itemId, updates: processedCachedColumn } });
                // +++ Add logging for state set after cache hit +++
                const cachedColumns = detailsCache[currentTableId]?.columns?.filter((col: any) =>
                    col.draftDescription || col.currentDescription || (col.metadata && Object.keys(col.metadata).length > 0)
                ) || [];
                // +++ START: Fix index calculation using column name +++
                // const cachedColIndex = cachedColumns.findIndex(col => col.id === itemId); // Incorrect
                const columnNameFromId = itemId.split('#column.')[1]; // Already fixed
                const cachedColIndex = columnNameFromId 
                    ? cachedColumns.findIndex(col => col.name?.endsWith(`.${columnNameFromId}`))
                    : -1;
                // +++ END: Fix index calculation +++
                setTaggedColumns(cachedColumns);
                // intendedColumnIdRef.current = cachedDetails.id; // --- Remove Ref ---
                setCurrentColumnIndex(cachedColIndex); // Set index based on name match
                setIsColumnDataReady(true);
                console.log('[loadItemDetails - Cache Hit - Column] Set State:', { taggedColumnsCount: cachedColumns.length, currentColumnIndex: cachedColIndex, isColumnDataReady: true });
                // Return processed data instead of raw cache
                return processedCachedColumn; 
                // +++ END: Explicitly process cached column details +++
            }
        } else {
          // Use cached table details
          // Check if cached table data has 'columns'
          if (!cachedDetails || !cachedDetails.columns) {
              console.warn(`[loadItemDetails - Cache Hit - Table] Stale cache (missing columns) for ${itemId}. Forcing API refresh.`);
              isCacheValid = false; // Mark cache as invalid
          } else {
              // --- Cache is VALID: Process Table ---
              console.log('[loadItemDetails - Cache Hit - Table] Dispatching update for Table ID:', itemId);
              dispatch({ type: 'UPDATE_ITEM', payload: { id: itemId, updates: { ...cachedDetails, currentColumn: null } } }); // Ensure currentColumn is null
              // Update local component state related to columns based on cache
              if (cachedDetails.columns) {
                  const columnsWithMetadata = cachedDetails.columns.filter((col: any) =>
                    col.draftDescription || col.currentDescription || (col.metadata && Object.keys(col.metadata).length > 0)
                  );
                  setTaggedColumns(columnsWithMetadata);
                  console.log('[loadItemDetails - Cache Hit - Table] Set taggedColumns, count:', columnsWithMetadata.length);
              } else {
                  setTaggedColumns([]); // Ensure reset if no columns in cache
                  console.log('[loadItemDetails - Cache Hit - Table] Reset taggedColumns (empty in cache)');
              }
              setCurrentColumnIndex(-1); // Reset column index when loading table
              setCurrentItemId(itemId); // Set current item ID to the table ID
              setIsColumnDataReady(true); // Table data is ready, maybe columns aren't needed yet? Or should this be false? Let's assume true for now.
              console.log('[loadItemDetails - Cache Hit - Table] Set State:', { currentColumnIndex: -1, isColumnDataReady: true });
              return cachedDetails; // Return valid cached data
          }
        }
        // If cache was marked invalid, execution will continue outside this block
        if (!isCacheValid) {
            console.log(`[loadItemDetails] Cache for ${itemId} was invalid, proceeding to API fetch.`);
        }
      } // <-- End of cache check block

      // --- API Fetch --- 
      // Code will fall through to here if cache is stale, not present, or forceRefresh=true
      console.log('[loadItemDetails] Fetching details from API for:', itemId);
      const [projectId, datasetId, tableIdBase] = currentTableId.replace('#table', '').split('.');
      const columnName = isColumn ? itemId.split('#column.')[1] : undefined;

      const response = await axios.post(
        `${normalizedBaseUrl()}/metadata/review/details`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          table_settings: {
            project_id: projectId,
            dataset_id: datasetId,
            table_id: tableIdBase, // Use base ID for API
          },
          // API fetches table details even when asking for a column, we extract later
        },
        getAxiosRequestConfig(apiToken)
      );

      const details = response.data; // This is always the full table details from API
      console.log('[loadItemDetails] Received raw details from API:', details);

      let finalItemDetails: MetadataItem | null = null;
      let stateUpdateId = itemId; // ID for dispatching state update

      if (isColumn) {
        // --- Processing Column ---
        const extractedColumnDetails = details.columns?.find((col: any) => col.name.endsWith(`.${columnName}`));
        if (!extractedColumnDetails) {
          throw new Error(`Column ${columnName} not found in API response for table ${currentTableId}`);
        }
        console.log('[loadItemDetails] Extracted raw column details:', extractedColumnDetails);

        const columnMetadata = extractedColumnDetails.metadata || {};
        const acceptedStatus = columnMetadata['is-accepted'];
        const acceptedTimestamp = columnMetadata['when-accepted'];
        const isItemAccepted = acceptedStatus === true || String(acceptedStatus).toLowerCase() === 'true';

        finalItemDetails = {
          // Base fields from extracted column data
          id: itemId, // Use the full column ID
          type: 'column',
          name: extractedColumnDetails.name,
          currentDescription: extractedColumnDetails.currentDescription || '',
          draftDescription: extractedColumnDetails.draftDescription || '',
          isHtml: extractedColumnDetails.isHtml || false,
          comments: extractedColumnDetails.comments || [],
          tags: extractedColumnDetails.tags || {},
          
          // Construct the metadata object, merging acceptance status
          metadata: {
            ...columnMetadata, // Include all original metadata
            'is-accepted': isItemAccepted, // Ensure correct value type might be needed if API returns string
            'when-accepted': acceptedTimestamp,
          },

          // Set top-level calculated fields
          isAccepted: isItemAccepted,
          whenAccepted: acceptedTimestamp,
          status: isItemAccepted ? 'accepted' : (extractedColumnDetails.status || 'draft'),

          // Other relevant fields
          lastModified: extractedColumnDetails.lastModified || columnMetadata.generation_date || new Date().toISOString(),
          markedForRegeneration: extractedColumnDetails.markedForRegeneration || columnMetadata.to_be_regenerated || false,
          isMarkingForRegeneration: false, // Reset loading state
          // tableMarkedForRegeneration: details.markedForRegeneration || details.metadata?.['to-be-regenerated'] || false, // Removed - Not part of item state
          parentTableId: currentTableId // Add parent ID if needed
        };

        // Update the parent table item in state to hold this column as current
        if (finalItemDetails) { // Add null check before dispatch
          // --- Add logging before dispatch --- START
          console.log('[loadItemDetails - Column] Pre-dispatch check:', {
              finalItemDetails_isAccepted: finalItemDetails.isAccepted,
              finalItemDetails_status: finalItemDetails.status,
              finalItemDetails_metadata_exists: !!finalItemDetails.metadata,
              finalItemDetails_metadata_content: JSON.stringify(finalItemDetails.metadata)
          });
          // --- Add logging before dispatch --- END

          // Pre-dispatch check for column API 
          console.log('[loadItemDetails - Column] Pre-dispatch check:', {
            finalItemDetails_isAccepted: finalItemDetails.isAccepted,
            finalItemDetails_status: finalItemDetails.status,
            finalItemDetails_metadata_exists: Boolean(finalItemDetails.metadata),
            finalItemDetails_metadata_content: JSON.stringify(finalItemDetails.metadata)
          });

          // *** Add a critical check to ensure we don't override the column being viewed ***
          // First get the ID of the column ACTUALLY being displayed right now
          const currentlyDisplayedColumnId = isColumnView ? 
            (currentItemId && currentColumnIndex >= 0 ? 
              `${currentItemId}#column.${taggedColumns[currentColumnIndex]?.name?.split('.').pop()}` : 
              null) : 
            null;
          
          // Check if we should update the display or just cache the data
          const loadedColumnId = finalItemDetails.id;
          const isNavigationActive = hasManuallySetColumnFlag.current;
          const shouldUpdateDisplay = currentlyDisplayedColumnId === loadedColumnId && !isNavigationActive;
          
          console.log('[loadItemDetails - Column] Display check:', {
            currentlyDisplayedColumnId,
            loadedColumnId,
            shouldUpdateDisplay,
            currentColumnIndex,
            isNavigationActive,
            hasManuallySetFlag: hasManuallySetColumnFlag.current
          });

          // Only update the parent table's currentColumn if the loaded column
          // is actually the one the user is viewing
          if (shouldUpdateDisplay) {
            console.log('[loadItemDetails - Column] Dispatching update for Table ID:', currentTableId, 'with currentColumn: details omitted');
            // Update the parent table state to include this column
            dispatch({
              type: 'UPDATE_ITEM',
              payload: { id: currentTableId, updates: { currentColumn: finalItemDetails } }
            });
          } else {
            console.log('[loadItemDetails - Column] Skipping parent table update since user is viewing a different column');
          }

          // Always update the column item in state regardless of display
          console.log('[loadItemDetails - Column] Dispatching update for Column ID:', finalItemDetails.id, 'with details: details omitted');
          dispatch({
            type: 'UPDATE_ITEM',
            payload: { id: finalItemDetails.id, updates: finalItemDetails }
          });
        } else {
            console.warn("[loadItemDetails] finalItemDetails is null, skipping column state dispatch for ID:", itemId);
        }

        // Update column list and index for navigation
         if (details.columns) {
            const columnsWithMetadata = details.columns.filter((col: any) => 
                col.draftDescription || col.currentDescription || (col.metadata && Object.keys(col.metadata).length > 0)
            );
            setTaggedColumns(columnsWithMetadata);
            const columnIndex = columnsWithMetadata.findIndex(col => col.name.endsWith(`.${columnName}`));
            if (columnIndex !== -1) {
                setCurrentColumnIndex(columnIndex);
            } else {
                setCurrentColumnIndex(-1); // Ensure reset if column not found in filtered list
            }
            console.log('[loadItemDetails - API Fetch - Column] Set taggedColumns (count:', columnsWithMetadata.length, '), currentColumnIndex:', columnIndex !== -1 ? columnIndex : -1);
        } else {
            setTaggedColumns([]);
            setCurrentColumnIndex(-1);
            console.log('[loadItemDetails - API Fetch - Column] Reset taggedColumns and currentColumnIndex (no columns in response)');
        }
        setCurrentItemId(currentTableId); // Ensure main item ID tracks the table
        setIsColumnDataReady(true); // <-- Set column data ready here
        console.log('[loadItemDetails - API Fetch - Column] Set isColumnDataReady: true');

      } else {
        // --- Processing Table ---
        const tableMetadata = details.metadata || {};
        const acceptedStatus = tableMetadata['is-accepted'];
        const acceptedTimestamp = tableMetadata['when-accepted'];
        const isItemAccepted = acceptedStatus === true || String(acceptedStatus).toLowerCase() === 'true';

        finalItemDetails = {
           // Base fields from table details
           id: itemId, // Table ID
           type: 'table',
           name: details.name,
           currentDescription: details.currentDescription || '',
           draftDescription: details.draftDescription || '',
           isHtml: details.isHtml || false,
           comments: details.comments || [],
           tags: details.tags || {},

           // Construct metadata, merging acceptance status
           metadata: {
               ...tableMetadata,
               'is-accepted': isItemAccepted,
               'when-accepted': acceptedTimestamp,
           },

           // Set top-level calculated fields
           isAccepted: isItemAccepted,
           whenAccepted: acceptedTimestamp,
           status: isItemAccepted ? 'accepted' : (details.status || 'draft'),

           // Other relevant fields
           lastModified: details.lastModified || tableMetadata.generation_date || new Date().toISOString(),
           markedForRegeneration: details.markedForRegeneration || tableMetadata.to_be_regenerated || false,
           isMarkingForRegeneration: false, // Reset loading state
           // columns: details.columns || [], // Removed - Not part of item state
           currentColumn: null // Ensure currentColumn is null for table view
        };

        if (finalItemDetails) { // Add null check before dispatch
          dispatch({ type: 'UPDATE_ITEM', payload: { id: itemId, updates: finalItemDetails } });
        } else {
            console.warn("[loadItemDetails] finalItemDetails is null, skipping table state dispatch for ID:", itemId);
        }

        // Update column list and index for navigation
        if (details.columns) {
            const columnsWithMetadata = details.columns.filter((col: any) => 
                col.draftDescription || col.currentDescription || (col.metadata && Object.keys(col.metadata).length > 0)
            );
            setTaggedColumns(columnsWithMetadata);
            console.log('[loadItemDetails - API Fetch - Table] Set taggedColumns, count:', columnsWithMetadata.length);
        } else {
            setTaggedColumns([]);
            console.log('[loadItemDetails - API Fetch - Table] Reset taggedColumns (empty in response)');
        }
        setCurrentColumnIndex(-1); // Reset column index
        setCurrentItemId(itemId); // Set current item ID to the table ID
        setIsColumnDataReady(true); // <-- Set column data ready here
        console.log('[loadItemDetails - API Fetch - Table] Set State:', { currentColumnIndex: -1, isColumnDataReady: true });
      }

      console.log('[loadItemDetails] Dispatching final processed details:', finalItemDetails);
      
      // --- Update Cache ---
      const newCache = { ...detailsCache };
      if (finalItemDetails) {
          // Cache the processed details (table or column) under its specific ID
          // +++ START: Ensure 'columns' array is included in the table cache entry +++
          let dataToCache = finalItemDetails;
          if (finalItemDetails.type === 'table') {
              dataToCache = {
                  ...finalItemDetails,
                  columns: details.columns || [] // Explicitly add the raw columns array here
              };
              console.log('[loadItemDetails - API Fetch - Table] Caching table data WITH columns array, count:', (details.columns || []).length);
          } else {
              console.log('[loadItemDetails - API Fetch - Column] Caching column data.');
          }
          newCache[finalItemDetails.id] = dataToCache;
          // +++ END: Ensure 'columns' array is included in the table cache entry +++

          // If it was a table, also cache its raw column data if needed elsewhere
          // (Avoid caching processed columns here to prevent staleness)
          if (finalItemDetails.type === 'table' && details.columns) {
              details.columns.forEach((col: any) => {
                  const colId = `${finalItemDetails!.id}#column.${col.name.split('.').pop()}`; // Add ! assertion
                  if (!newCache[colId]) { // Only add if not already cached (e.g. by direct column load)
                      newCache[colId] = { 
                          ...col, 
                           // Add minimal processing if needed, e.g., ensure metadata exists
                           metadata: col.metadata || {}, 
                          tableMarkedForRegeneration: finalItemDetails!.markedForRegeneration || false // Add ! assertion
                        };
                  }
              });
          }
           // If it was a column, ensure the parent table entry in cache reflects this potentially updated column
            if (finalItemDetails.type === 'column' && newCache[currentTableId]) {
               // This logic might be complex - ensure you're not overwriting other columns
               // It might be safer to just cache the column itself and let the table reload fully if needed
               // Example: Update the columns array in the cached table data
               // const parentTableCache = newCache[currentTableId];
               // parentTableCache.columns = parentTableCache.columns?.map(c => c.id === finalItemDetails.id ? finalItemDetails : c) || [];
            }
      }
      setDetailsCache(newCache);

      return finalItemDetails; // Return the processed data

    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || 'Failed to load item details';
      console.error('[loadItemDetails] Error loading item details:', error);
      setError(errorMessage);
      setIsColumnDataReady(false); // <-- Reset readiness on error
      console.log('[loadItemDetails] Error path - Set isColumnDataReady: false');
      return null;
    } finally {
      // Always ensure readiness is false if loader was shown and finished, let success path set it true
      if (showLoader) {
          //setIsColumnDataReady(false); // <-- REMOVE THIS LINE (Commented out in previous step)
          setIsLoadingDetails(false);
          console.log('[loadItemDetails] Finally block: Set isLoadingDetails: false');
      } else {
          console.log('[loadItemDetails] Finally block: No loader shown, state unchanged here.');
      }
    }
  }; // End of loadItemDetails

  const preloadNextItem = async (currentIndex: number) => {
    if (currentIndex < items.length - 1) {
      // Preload the next item
      const nextItemIndex = currentIndex + 1;
      const nextItem = items[nextItemIndex];
      console.log('Preloading next item:', nextItem.id, 'at index:', nextItemIndex);
      
      try {
        // Only preload if not already in cache
        if (!detailsCache[nextItem.id]) {
          await loadItemDetails(nextItem.id, false); // Use forceRefresh = false
          console.log('Successfully preloaded next item:', nextItem.id);
        } else {
          console.log('Next item already in cache:', nextItem.id);
        }
        
        // REMOVED: Lookahead by 2 preloading logic
        // if (nextItemIndex < items.length - 1) { ... }

      } catch (error) {
        console.error('Error preloading next item:', nextItem.id, error);
        // Don't bubble up error, preloading failure shouldn't block UI
      }
    }
  };

  const renderReviewMode = () => {
    const currentItem = items[currentItemIndex];
    if (!currentItem) return null;

    // Get the correct item to display - either the column or the table
    // We need to ensure we're using the most up-to-date data from the state
    let displayItem: MetadataItem;
    const isColumnView = Boolean(currentItem.currentColumn);
    
    // Update isColumnView state to match what we're actually displaying
    if (isColumnView !== isColumnView) {
      console.log('Updating isColumnView state to match current view:', isColumnView);
      setIsColumnView(isColumnView);
    }
    
    console.log('renderReviewMode called with:', {
      currentItemId: currentItem.id,
      hasCurrentColumn: Boolean(currentItem.currentColumn),
      currentColumnIndex,
      isColumnView
    });
    
    if (isColumnView && currentItem.currentColumn) {
      // First - determine which column SHOULD be displayed based on currentColumnIndex
      const targetColumnName = currentColumnIndex >= 0 && currentColumnIndex < taggedColumns.length ? 
        taggedColumns[currentColumnIndex].name.split('.').pop() : null;
      
      const targetColumnId = targetColumnName ? 
        `${currentItem.id}#column.${targetColumnName}` : null;
      
      // Check if currentColumn matches the expected column based on currentColumnIndex
      const currentColumnId = currentItem.currentColumn.id;
      
      console.log('renderReviewMode column check:', {
        targetColumnId,
        currentColumnId,
        currentColumnIndex,
        taggedColumnsLength: taggedColumns.length
      });
      
      // If we've got a mismatch between selected index and displayed column, 
      // we should try to get the correct column
      let columnData = currentItem.currentColumn;
      const isAcceptingAnyItem = Object.values(isAccepting).some(value => value === true);
      
      // If there's a process running or a mismatch, we need to be extra careful about the display
      if (hasManuallySetColumnFlag.current || isAcceptingAnyItem || (targetColumnId && targetColumnId !== currentColumnId)) {
        console.log('Special handling needed for column display:', {
          hasManuallySetColumnFlag: hasManuallySetColumnFlag.current, 
          isAcceptingAnyItem,
          targetColumnId, 
          currentColumnId
        });
      }
      
      if (targetColumnId && targetColumnId !== currentColumnId) {
        console.log('MISMATCH: currentColumn does not match currentColumnIndex, attempting to find correct column');
        
        // Try to find the correct column from Redux state or cache
        const correctColumn = items.find(i => i.id === targetColumnId);
        if (correctColumn) {
          console.log('Found correct column in items array, using it instead');
          columnData = correctColumn;
        } else if (detailsCache[targetColumnId]) {
          console.log('Found correct column in cache, using it instead');
          columnData = detailsCache[targetColumnId];
        } else {
          console.warn('Could not find correct column, continuing with current column');
        }
      }
      
      // We're viewing a column
      // Cast columnData to extended MetadataItem to satisfy TypeScript
      const currentColumnData = columnData as MetadataItem;
      displayItem = {
        ...currentColumnData,
        // Ensure we have all required properties
        id: currentColumnData.id || (targetColumnId || `${currentItem.id}#column.${currentColumnData.name.split('.').pop()}`),
        type: 'column', // Always set type to column when viewing a column
        parentTableId: currentItem.id,
        // Ensure acceptance status is explicitly carried over
        isAccepted: currentColumnData.isAccepted, 
        whenAccepted: currentColumnData.whenAccepted 
      };
      console.log('Rendering column view:', displayItem.id);
    } else {
      // We're viewing a table
      displayItem = {
        ...currentItem,
        // Ensure currentColumn is null
        currentColumn: null,
        type: 'table' // Always set type to table when viewing a table
      };
      console.log('Rendering table view:', displayItem.id);
    }

    // Add more detailed logging to help with debugging
    console.log('Display item details:', {
      id: displayItem.id,
      type: displayItem.type,
      isColumnView,
      hasCurrentColumn: Boolean(currentItem.currentColumn),
      name: displayItem.name,
      status: displayItem.status,
      hasDraftDescription: Boolean(displayItem.draftDescription),
      hasCurrentDescription: Boolean(displayItem.currentDescription)
    });

    // Verify that displayItem has the necessary data
    if (!displayItem.id) {
      console.error('Invalid displayItem:', displayItem);
      setError('Display item is missing required data. Please refresh the page.');
      return null;
    }

    if (isLoadingDetails) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '400px' }}>
          <CircularProgress />
        </Box>
      );
    }

    // Debug information
    console.log('Review Mode State:', {
      currentItemId,
      displayItemId: displayItem.id,
      isColumnView,
      currentColumnIndex,
      taggedColumnsCount: taggedColumns.length
    });

    // Determine if descriptions have changed
    const hasChanges = displayItem.currentDescription !== displayItem.draftDescription;

    return (
      <Box>
        {/* Top Sticky Action Bar */}
        <Box sx={{ 
          p: 2, 
          mb: 2,
          position: 'sticky',
          top: 64,
          zIndex: 1,
          backgroundColor: 'background.paper',
          borderBottom: 1,
          borderColor: 'divider'
        }}>
          {renderActionBar(currentItem)}
        </Box>

        {/* Main Content */}
        <Box sx={{ pb: 8 }}>
          <Paper sx={{ p: 2, mb: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="h5" gutterBottom>
                  {isColumnView ? (
                    <>
                      Column: {displayItem.name.split('.').pop()}
                      <Typography component="span" variant="h5" color="text.secondary" sx={{ ml: 2 }}>
                        • Table: {displayItem.name.split('#')[0]}
                      </Typography>
                    </>
                  ) : (
                    displayItem.name
                  )}
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
                  <Chip
                    label={displayItem.type.charAt(0).toUpperCase() + displayItem.type.slice(1)}
                    size="small"
                    color={displayItem.type === 'table' ? 'primary' : 'secondary'}
                  />
                  <Chip
                    label={displayItem.status.charAt(0).toUpperCase() + displayItem.status.slice(1)}
                    size="small"
                    color={getStatusColor(displayItem.status)}
                  />
                  {(displayItem.markedForRegeneration) && (
                    <Chip
                      label="Marked for Regeneration"
                      size="small"
                      sx={{
                        backgroundColor: 'warning.main',
                        color: 'warning.contrastText'
                      }}
                      icon={<AutorenewIcon />}
                    />
                  )}
                  {/* START: Add dedicated chip for accepted status */}
                  {displayItem.isAccepted && (
                    <Tooltip title={displayItem.whenAccepted ? `Accepted on ${new Date(displayItem.whenAccepted).toLocaleString()}` : 'Accepted'}>
                      <Chip 
                        label={`Accepted`}
                        size="small"
                        color="success"
                        variant="filled"
                        icon={<CheckCircleIcon fontSize="small" />} 
                      />
                    </Tooltip>
                  )}
                  {/* END: Add dedicated chip for accepted status */}
                  <Typography variant="body2" color="text.secondary">
                    Last modified: {new Date(displayItem.lastModified).toLocaleString()}
                  </Typography>
                </Box>
              </Box>
            </Box>

            {/* Remove the column navigation bar and context bar since we're showing column info in the header */}
            {/* Tags Section */}
            {displayItem.tags && Object.keys(displayItem.tags).length > 0 && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="h6" gutterBottom>
                  Tags
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  {Object.entries(displayItem.tags).map(([key, value]) => (
                    <Chip
                      key={key}
                      label={`${key}: ${value}`}
                      variant="outlined"
                      sx={{ maxWidth: 200 }}
                    />
                  ))}
                </Box>
              </Box>
            )}
          </Paper>

          <Grid container spacing={2}>
            <Grid item xs={12}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant="h6">Description Changes</Typography>
                <FormControlLabel
                  control={
                    <Switch
                      checked={showDiff}
                      onChange={(e) => setShowDiff(e.target.checked)}
                    />
                  }
                  label={showDiff ? "Showing Diff View" : "Showing Side by Side"}
                />
              </Box>
              {showDiff ? (
                <Box sx={{ 
                  minHeight: '400px',
                  '& .diff-viewer': {
                    width: '100%',
                    minHeight: '400px'
                  },
                  '& .diff-container': {
                    margin: 0,
                    padding: '16px'
                  },
                  '& .diff-title-header': {
                    padding: '8px 16px',
                    background: '#f5f5f5',
                  }
                }}>
                  <ReactDiffViewer
                    oldValue={displayItem.currentDescription || ''}
                    newValue={displayItem.draftDescription || ''}
                    splitView={true}
                    leftTitle="Current Version"
                    rightTitle="Draft Version"
                    compareMethod={DiffMethod.WORDS}
                    styles={{
                      variables: {
                        light: {
                          diffViewerBackground: '#f8f9fa',
                          diffViewerColor: '#212529',
                          addedBackground: '#e6ffec',
                          addedColor: '#24292f',
                          removedBackground: '#ffebe9',
                          removedColor: '#24292f',
                          wordAddedBackground: '#abf2bc',
                          wordRemovedBackground: '#fdb8c0',
                        }
                      }
                    }}
                  />
                </Box>
              ) : (
                <Grid container spacing={2}>
                  <Grid item xs={6}>
                    <Typography variant="h6" gutterBottom>Current Version</Typography>
                    <Paper sx={{ p: 2, minHeight: '200px' }}>
                      {displayItem.isHtml ? (
                        <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(displayItem.currentDescription || 'No description available') }} />
                      ) : (
                        <Typography>{displayItem.currentDescription || 'No description available'}</Typography>
                      )}
                    </Paper>
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="h6" gutterBottom>Draft Version</Typography>
                    <Paper sx={{ p: 2, minHeight: '200px', bgcolor: hasChanges ? 'rgba(200, 250, 205, 0.15)' : undefined }}>
                      {displayItem.isHtml ? (
                        <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(displayItem.draftDescription || 'No draft description available') }} />
                      ) : (
                        <Typography>{displayItem.draftDescription || 'No draft description available'}</Typography>
                      )}
                    </Paper>
                  </Grid>
                </Grid>
              )}
            </Grid>
          </Grid>

          {/* Comments Section */}
          <Card sx={{ mt: 2 }}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Comments
              </Typography>
              {renderComments(displayItem.comments)}
              <Box sx={{ mt: 2 }}>
                <TextField
                  fullWidth
                  multiline
                  rows={3}
                  variant="outlined"
                  placeholder="Add a comment..."
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                  onKeyDown={(e: ReactKeyboardEvent<HTMLDivElement>) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleAddComment(displayItem.id);
                    }
                  }}
                />
                <Button
                  variant="contained"
                  onClick={() => handleAddComment(displayItem.id)}
                  disabled={!newComment.trim()}
                  sx={{ mt: 1 }}
                >
                  Add Comment
                </Button>
              </Box>
            </CardContent>
          </Card>

          {/* DEBUG Section - Remove before production */}
          <Card sx={{ mt: 2, bgcolor: 'grey.100' }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant="h6" color="error">
                  DEBUG INFO - Remove before production
                </Typography>
                <Chip label="DEBUG" color="error" />
              </Box>
              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <Typography variant="subtitle1" gutterBottom>Raw Item Data:</Typography>
                  <Paper sx={{ p: 2, bgcolor: 'background.paper' }}>
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify({
                        id: displayItem.id,
                        type: displayItem.type,
                        metadata: displayItem.metadata,
                        tags: displayItem.tags,
                        markedForRegeneration: displayItem.markedForRegeneration,
                        isMarkingForRegeneration: displayItem.isMarkingForRegeneration,
                        status: displayItem.status,
                        draftDescription: Boolean(displayItem.draftDescription),
                        currentDescription: Boolean(displayItem.currentDescription),
                      }, null, 2)}
                    </pre>
                  </Paper>
                </Grid>
                {displayItem.type === 'column' && (
                  <Grid item xs={12}>
                    <Typography variant="subtitle1" gutterBottom>Column-specific Debug Info:</Typography>
                    <Paper sx={{ p: 2, bgcolor: 'background.paper' }}>
                      <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {/* --- Fix Debug Panel Metadata --- START */}
                        {JSON.stringify(
                            // Display the actual metadata object from the displayItem
                            displayItem.metadata || {} // <-- Display the metadata object directly
                        , (key, value) => {
                            // Optional filter: You might want to hide large fields like 'profile' here
                            // if (key === 'profile' && value) return '[Profile Data Hidden]';
                            return value;
                        }, 2)}
                        {/* --- Fix Debug Panel Metadata --- END */}
                      </pre>
                    </Paper>
                  </Grid>
                )}
              </Grid>
            </CardContent>
          </Card>
        </Box>
      </Box>
    );
  };

  const handleEditorChange = (content: string) => {
    if (editItem) {
      setEditItem((prev) =>
        prev ? { 
          ...prev, 
          draftDescription: content, 
          isHtml: isRichText 
        } : null
      );
    }
  };

  const handleRichTextToggle = (toRichText: boolean) => {
    const description = editItem?.draftDescription;
    if (!description) {
      setIsRichText(toRichText);
      return;
    }
    
    const content = toRichText
      ? description
          .split('\n')
          .map(line => `<p>${escapeHtmlText(line)}</p>`)
          .join('')
      : stripHtmlToText(description);
    
    handleEditorChange(content);
    setIsRichText(toRichText);
  };

  const fetchReviewItems = async (nextPageToken?: string): Promise<boolean> => { // Return boolean indicating success
    try {
      setError(null);
      setIsLoading(true);
      console.log('[fetchReviewItems] Starting fetch...'); // <-- Add log
      console.log('Fetching review items...');
      
      const response = await axios.post(
        `${normalizedBaseUrl()}/metadata/review`,
        {
          client_settings: {
            project_id: config.project_id,
            llm_location: config.llm_location,
            dataplex_location: config.dataplex_location,
          },
          dataset_settings: {
            project_id: config.project_id,
            dataset_id: config.dataset_id || "",
            documentation_csv_uri: "",
            strategy: "NAIVE"
          }
          // Add page_token logic here if/when pagination is implemented
        },
        getAxiosRequestConfig(apiToken)
      );

      console.log('[fetchReviewItems] Received response:', response.data); // <-- Add log
      console.log('Received response:', response.data);

      // Ensure we have a properly structured response
      const { items: newItems = [], nextPageToken: newPageToken = null, totalCount: newTotalCount = 0 } = response.data;
      
      console.log(`Processing ${newItems.length} items with totalCount: ${newTotalCount}`);

      dispatch({
        type: 'SET_ITEMS',
        payload: {
          items: newItems,
          pageToken: newPageToken,
          totalCount: newTotalCount
        }
      });

      // REMOVED: Logic to load/preload details directly from here
      // if (viewMode === 'review' && newItems.length > 0) {
      //   await loadItemDetails(newItems[0].id);
      //   if (newItems.length > 1) {
      //     preloadNextItem(0);
      //   }
      // }
      console.log('[fetchReviewItems] Fetch successful, returning true'); // <-- Add log
      return true; // Indicate success

    } catch (error: any) {
      console.error('[fetchReviewItems] Error during fetch:', error); // <-- Add log
      console.error('Error fetching review items:', error);
      setError(error.response?.data?.detail || 'Failed to fetch review items');
      // Set empty items on error to prevent undefined errors
      dispatch({
        type: 'SET_ITEMS',
        payload: {
          items: [],
          pageToken: null,
          totalCount: 0
        }
      });
      console.log('[fetchReviewItems] Fetch failed, returning false'); // <-- Add log
      return false; // Indicate failure
    } finally {
      // Setting isLoading false is handled by the calling useEffect
      // setIsLoading(false);
    }
  };

  // Add a debugging function
  const logDebugInfo = () => {
    const currentItem = items[currentItemIndex];
    console.group('ReviewPage Debug Information');
    console.log('Current State:', {
      viewMode,
      currentItemIndex,
      currentItemId,
      isColumnView: Boolean(currentItem?.currentColumn),
      taggedColumnsCount: taggedColumns.length,
      currentColumnIndex
    });
    
    if (currentItem) {
      console.log('Current Item:', {
        id: currentItem.id,
        type: currentItem.type,
        name: currentItem.name,
        hasCurrentColumn: Boolean(currentItem.currentColumn),
        currentColumnId: currentItem.currentColumn?.id
      });
    }
    
    console.log('Redux State:', state);
    console.log('Cache State:', {
      cacheKeys: Object.keys(detailsCache),
      currentItemInCache: currentItem ? Boolean(detailsCache[currentItem.id]) : false
    });
    console.groupEnd();
  };

  // Add this useEffect before the return statement
  useEffect(() => {
    // Clear accepting state whenever navigation changes
    // This ensures Accept buttons aren't greyed out after navigating
    setIsAccepting({});
    console.log('Navigation changed - clearing accepting state');
  }, [currentItemId, currentColumnIndex, isColumnView]);

  // Add a useEffect to monitor isRefreshing state changes
  useEffect(() => {
    console.log(`🔍 isRefreshing CHANGED to: ${isRefreshing}`, {
      stack: new Error().stack
    });
  }, [isRefreshing]);

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h1">
          Metadata Review
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={handleViewModeChange}
            sx={{ 
              mr: 2,
              height: 36.5,
              '& .MuiToggleButton-root': {
                padding: '6px 16px',
                textTransform: 'none',
              }
            }}
          >
            <ToggleButton value="list">
              <ViewListIcon sx={{ mr: 1 }} />
              List
            </ToggleButton>
            <ToggleButton value="review">
              <RateReviewIcon sx={{ mr: 1 }} />
              Review
            </ToggleButton>
          </ToggleButtonGroup>
          <Button
            variant="contained"
            startIcon={<RefreshIcon sx={{ 
              animation: isRefreshing ? 'spin 1s linear infinite' : 'none',
              '@keyframes spin': {
                '0%': { transform: 'rotate(0deg)' },
                '100%': { transform: 'rotate(360deg)' }
              }
            }} />}
            onClick={handleRefresh}
            disabled={isRefreshing}
          >
            {isRefreshing ? 'Refreshing...' : 'Refresh'}
          </Button>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {typeof error === 'object' ? JSON.stringify(error) : error}
        </Alert>
      )}

      {isRefreshing && viewMode === 'list' && (
        <Alert severity="info" sx={{ mb: 3 }}>
          Fetching review items from Dataplex catalog...
        </Alert>
      )}

      {!isRefreshing && items.length === 0 && (
        <Alert severity="info" sx={{ mb: 3 }}>
          No items found for review.
        </Alert>
      )}

      {viewMode === 'list' ? renderListMode() : renderReviewMode()}

      <Dialog
        open={editDialogOpen}
        onClose={handleCloseEditDialog}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            Edit Metadata
            <Box sx={{ 
              display: 'flex', 
              alignItems: 'center',
              bgcolor: 'background.paper',
              border: 1,
              borderColor: 'divider',
              borderRadius: 1,
              p: 0.5
            }}>
              <Button
                size="small"
                variant={isRichText ? "text" : "contained"}
                onClick={() => {
                  const description = editItem?.draftDescription;
                  if (isRichText && description) {
                    // Convert HTML to plain text
                    const plainText = stripHtmlToText(description);
                    handleEditorChange(plainText);
                  }
                  setIsRichText(false);
                }}
                sx={{ minWidth: '90px' }}
              >
                Plain Text
              </Button>
              <Button
                size="small"
                variant={isRichText ? "contained" : "text"}
                onClick={() => {
                  const description = editItem?.draftDescription;
                  if (!isRichText && description) {
                    // Convert plain text to basic HTML safely
                    const htmlContent = sanitizeHtml(
                      description
                        .split('\n')
                        .map(line => `<p>${escapeHtmlText(line)}</p>`)
                        .join('')
                    );
                    handleEditorChange(htmlContent);
                  }
                  setIsRichText(true);
                }}
                sx={{ minWidth: '90px' }}
              >
                Rich Text
              </Button>
            </Box>
          </Box>
        </DialogTitle>
        <DialogContent>
          {(editItem?.type === 'column' || editItem?.id?.includes('#column.')) && (
            <Alert severity="warning" sx={{ mt: 1, mb: 2 }}>
              Editing column draft descriptions cannot be saved because the backend API does not implement /update_column_draft_description.
            </Alert>
          )}
          {editItem && (
            <Box sx={{ pt: 2 }}>
          {isRichText ? (
            <ReactQuill
                  theme="snow"
                  value={sanitizeHtml(editItem.draftDescription || '')}
              onChange={handleEditorChange}
                  modules={{
                    toolbar: [
                      [{ 'header': [1, 2, false] }],
                      ['bold', 'italic', 'underline', 'strike', 'blockquote'],
                      [{'list': 'ordered'}, {'list': 'bullet'}],
                      ['link', 'clean']
                    ],
                  }}
                  formats={[
                    'header',
                    'bold', 'italic', 'underline', 'strike', 'blockquote',
                    'list', 'bullet',
                    'link'
                  ]}
                  style={{
                    height: '300px',
                    marginBottom: '50px',
                    display: 'flex',
                    flexDirection: 'column'
                  }}
            />
          ) : (
            <TextField
                  label="Draft Description"
                  value={editItem.draftDescription}
                  onChange={(e) => handleEditorChange(e.target.value)}
              multiline
                  rows={15}
              fullWidth
                  sx={{ mb: 2 }}
            />
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseEditDialog}>Cancel</Button>
          <Button
            onClick={handleSaveEdit}
            variant="contained"
            disabled={editItem?.type === 'column' || editItem?.id?.includes('#column.')}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ReviewPage; 