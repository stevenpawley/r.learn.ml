#!/usr/bin/env python
from __future__ import absolute_import, print_function

import os
from subprocess import PIPE

import grass.script as gs
import numpy as np
import pandas as pd
from grass.pygrass.gis.region import Region
from grass.pygrass.modules.shortcuts import imagery as im
from grass.pygrass.modules.shortcuts import raster as r
from grass.pygrass.modules.shortcuts import vector as v
from grass.pygrass.raster import RasterRow, numpy2raster
from grass.pygrass.raster.buffer import Buffer
from grass.pygrass.utils import get_mapset_raster, get_raster_for_points
from grass.pygrass.vector import VectorTopo
from indexing import ExtendedDict, LinkedList


class RasterStack(object):

    def __init__(self, rasters=None, group=None):
        """A RasterStack enables a collection of raster layers to be bundled
        into a single RasterStack object.
        
        Parameters
        ----------
        rasters : list, str
            List of names of GRASS GIS raster maps. Note that although the
            maps can reside in different mapsets, they cannot have the same
            names.
        
        group : str (opt)
            Create a RasterStack from rasters contained in a GRASS GIS imagery
            group. This parameter is mutually exclusive with the `rasters`
            parameter.
        
        Attributes
        ----------
        loc : dict
            Name-based indexing of RasterRow objects within the RasterStack.
        
        iloc : int
            Index-based indexing of RasterRow objects within the RasterStack.
                        
        mtypes : dict
            Dict of key, value pairs of full_names and GRASS data types.
        
        count : int
            Number of RasterRow objects within the RasterStack.
        """

        self.loc = ExtendedDict(self)           # label-based indexing
        self.iloc = LinkedList(self, self.loc)  # integer-based indexing
        
        # key, value pairs of full name and GRASS data type
        self.mtypes = {}
        self.count = 0
        self._categorical_idx = []
        self._cell_nodata = -2147483648
        
        # some checks
        if rasters and group:
            gs.fatal('arguments "rasters" and "group" are mutually exclusive')
        
        if group:
            map_list = im.group(group=group, flags=["l", "g"], quiet=True, 
                                stdout_=PIPE)
            rasters = map_list.outputs.stdout.split(os.linesep)[:-1]
        
        self.layers = rasters  # call property
        
    def __getitem__(self, label):
        """Subset the RasterStack object using a label or list of labels.
        
        Parameters
        -----------
        label : str, list
            Label or list of labels representing RasterRow objects within the
            stack.
            
        Returns
        -------
        RasterStack
            A new RasterStack object only containing the subset of layers 
            specified in the label argument.
        """
        
        if isinstance(label, str):
            label = [label]
        
        subset_layers = []
        
        for i in label:
            
            if i in self.names is False:
                raise KeyError('layername not present in Raster object')
            else:
                subset_layers.append(self.loc[i])
            
        subset_raster = RasterStack(subset_layers)
        subset_raster.rename(
            {old : new for old, new in zip(subset_raster.names, label)})
        
        return subset_raster

    def __setitem__(self, key, value):
        """Replace a RasterLayer within the Raster object with a new 
        RasterLayer.
        
        Note that this modifies the Raster object in place.
        
        Parameters
        ----------
        key : str
            Key-based index of layer to be replaced.
        
        value : grass.pygrass.raster.RasterRow
            RasterRow to use for replacement.
        """
        
        self.loc[key] = value
        self.iloc[self.names.index(key)] = value
        setattr(self, key, value)

    def __iter__(self):
        """Iterate over grass.pygrass.raster.RasterRow objects.
        """
        return(iter(self.loc.items()))
    
    @property
    def names(self):
        """Return the names of the grass.pygrass.raster.RasterRow objects in 
        the RasterStack.
        """
        
        names = []
        for src in self.loc.values():
            names.append(src.fullname())
        
        return list(names)

    @property
    def layers(self):
        return self.loc

    @layers.setter
    def layers(self, mapnames):
        """Setter method for the layers attribute in the RasterStack.
        
        Parameters
        ----------
        mapnames : str, list
            Name or list of names of GRASS GIS rasters to add to the
            RasterStack object.
        """
        
        # some checks
        if isinstance(mapnames, str):
            mapnames = [mapnames]
        
        # reset existing attributes
        for name in list(self.layers.keys()):
            delattr(self, name)

        self.loc = ExtendedDict(self)
        self.iloc = LinkedList(self, self.loc)
        self.count = len(mapnames)
        self.mtypes = {}

        # split raster name from mapset name
        raster_names = [i.split('@')[0] for i in mapnames]
        mapset_names = [get_mapset_raster(i) for i in mapnames]
        
        # add rasters and metadata to stack
        for name, mapset in zip(raster_names, mapset_names):
            
            with RasterRow(name=name, mapset=mapset) as src:

                if src.exist() is True:
    
                    ras_name = src.name.split('@')[0]  # name sans mapset
                    full_name = src.name_mapset()      # name with mapset
                    valid_name = ras_name.replace('.', '_')
                    
                    # grass gis raster could have same name if in diff mapset
                    if valid_name in list(self.layers.keys()):
                        raise ValueError(
                                'Cannot append map {name} to the '
                                'RasterStack because a map with the same name '
                                'already exists'.format(name=ras_name))
    
                    self.mtypes.update({full_name: src.mtype})    
                    self.loc[valid_name] = src    
                    setattr(self, valid_name, src)
                
                else:
                    gs.fatal('GRASS raster map ' + r + ' does not exist')
    
    @property
    def categorical(self):
        return self._categorical_idx
    
    @categorical.setter
    def categorical(self, names):
        """Update the RasterStack categorical map indexes.
        """
        
        if isinstance(names, str):
            names = [names]
            
        indexes = []
        
        # check that each category map is also in the imagery group
        for n in names:
            
            try:
                indexes.append(self.names.index(n))
            
            except ValueError:
                gs.fatal('Category map {0} not in the imagery group'.format(n))
        
        self._categorical_idx = indexes


    def append(self, other, in_place=True):
        """Setter method to add new RasterRows to a RasterStack object.
        
        Note that this modifies the Raster object in-place.

        Parameters
        ----------
        other : str or list
            Name of GRASS GIS raster, or list of names
        
        in_place : bool (opt). Default is True
            Whether to change the Raster object in-place or leave original and
            return a new Raster object.
        
        Returns
        -------
        RasterStack
            Returned only if `in_place` is True
        """
        
        if isinstance(other, str):
            other = [other]
            
        if in_place is True:
            self.layers = list(self.layers.keys()) + other
            
        else:
            new_raster = RasterStack(
                rasters=[i for i in self.names] + other)
            return new_raster

    def drop(self, labels, in_place=True):
        """Drop individual RasterRow objects from the RasterStack.
        
        Note that this modifies the RasterStack object in-place by default.
        
        Parameters
        ---------
        labels : single label or list-like
            Index (int) or layer name to drop. Can be a single integer or
            label, or a list of integers or labels.
        
        in_place : bool (opt). Default is True
            Whether to change the RasterStack object in-place or leave original
            and return a new RasterStack object.

        Returns
        -------
        RasterStack
            Returned only if `in_place` is True
        """

        # convert single label to list
        if isinstance(labels, str):
            labels = [labels]
    
        subset_names = [fullname for fullname, mapname in 
                        zip(self.names, self.loc.keys())
                        if mapname not in labels]

        if in_place is True:
            self.layers = [i for i in subset_names]
        else:
            new_raster = RasterStack(rasters=[i for i in subset_names])
            
            return new_raster
    
    def read(self, row=None, window=None):
        """Read data from RasterStack as a masked 3D numpy array.
        
        Notes
        -----
        Read an entire RasterStack into a numpy array. If row or window is 
        supplied, then a single row, or a range of rows from 
        window = (start_row, end_row) is read into an array.

        Parameters
        ----------
        row : int (opt)
            Integer representing the index of a single row of a raster to read.

        window : tuple (opt)
            Tuple of integers representing the start and end numbers of rows to
            read as a single block of rows.

        Returns
        -------
        
        data : ndarray
            3d masked numpy array containing data from RasterStack rasters.
        """

        reg = Region()

        # create numpy array to receive data based on row/window/dataset size
        if window:
            row_start, row_stop = window
            width = reg.cols
            height = abs(row_stop-row_start)
            shape = (self.count, height, width)

        elif row:
            row_start = row
            row_stop = row+1
            height = 1
            shape = (self.count, height, reg.cols)
        
        else:
            shape = (self.count, reg.rows, reg.cols) 

        data = np.zeros(shape)

        if row or window:
            rowincrs = [i for i in range(row_start, row_stop)]

        # read from each RasterRow object
        for band, (name, src) in enumerate(self.layers.items()):
            try:
                with RasterRow(src.fullname()) as f:
                    if row or window:
                        for i, row in enumerate(rowincrs):
                            data[band, i, :] = f[row]
                    else:
                        data[band, :, :] = np.asarray(f)
            except:
                gs.fatal('Cannot read from raster {0}'.format(src.fullname))
        
        # mask array
        data = np.ma.masked_equal(data, self._cell_nodata)
        data = np.ma.masked_invalid(data)

        if isinstance(data.mask, np.bool_):
            mask_arr = np.empty(data.shape, dtype='bool')
            mask_arr[:] = False
            data.mask = mask_arr

        return data
    
    @staticmethod
    def _pred_fun(img, estimator):
        """Prediction function for classification or regression response.

        Parameters
        ----
        img : numpy.ndarray
            3d ndarray of raster data with the dimensions in order of
            (band, rows, columns).

        estimator : estimator object implementing 'fit'
            The object to use to fit the data.

        Returns
        -------
        numpy.ndarray
            2d numpy array representing a single band raster containing the
            classification or regression result.
        """
        n_features, rows, cols = img.shape[0], img.shape[1], img.shape[2]

        # reshape each image block matrix into a 2D matrix
        # first reorder into rows,cols,bands(transpose)
        # then resample into 2D array (rows=sample_n, cols=band_values)
        n_samples = rows * cols
        flat_pixels = img.transpose(1, 2, 0).reshape(
            (n_samples, n_features))

        # create mask for NaN values and replace with number
        flat_pixels_mask = flat_pixels.mask.copy()
        flat_pixels = np.ma.filled(flat_pixels, -99999)

        # prediction
        result = estimator.predict(flat_pixels)

        # replace mask and fill masked values with nodata value
        result = np.ma.masked_array(
            result, mask=flat_pixels_mask.any(axis=1))

        # reshape the prediction from a 1D matrix/list
        # back into the original format [band, row, col]
        result = result.reshape((1, rows, cols))
        
        return result
    
    @staticmethod
    def _prob_fun(img, estimator):
        """Class probabilities function.

        Parameters
        ----------
        img : numpy.ndarray
            3d numpy array of raster data with the dimensions in order of
            (band, row, column).

        estimator : estimator object implementing 'fit'
            The object to use to fit the data.

        Returns
        -------
        numpy.ndarray
            Multi band raster as a 3d numpy array containing the probabilities
            associated with each class. ndarray dimensions are in the order of
            (class, row, column).
        """
        n_features, rows, cols = img.shape[0], img.shape[1], img.shape[2]

        # reshape each image block matrix into a 2D matrix
        # first reorder into rows,cols,bands(transpose)
        # then resample into 2D array (rows=sample_n, cols=band_values)
        n_samples = rows * cols
        flat_pixels = img.transpose(1, 2, 0).reshape(
            (n_samples, n_features))

        # create mask for NaN values and replace with number
        flat_pixels_mask = flat_pixels.mask.copy()
        flat_pixels = np.ma.filled(flat_pixels, -99999)

        # predict probabilities
        result = estimator.predict_proba(flat_pixels)

        # reshape class probabilities back to 3D [iclass, rows, cols]
        result = result.reshape(
            (rows, cols, result.shape[1]))
        flat_pixels_mask = flat_pixels_mask.reshape(
            (rows, cols, n_features))

        # flatten mask into 2d
        mask2d = flat_pixels_mask.any(axis=2)
        mask2d = np.where(mask2d != mask2d.min(), True, False)
        mask2d = np.repeat(mask2d[:, :, np.newaxis],
                           result.shape[2], axis=2)

        # convert proba to masked array using mask2d
        result = np.ma.masked_array(
            result, mask=mask2d, fill_value=np.nan)

        # reshape band into raster format [band, row, col]
        result = result.transpose(2, 0, 1)

        return result
    
    @staticmethod
    def _predfun_multioutput(img, estimator):
        """Multi-target prediction function.

        Parameters
        ----------
        img : numpy.ndarray
            3d numpy array of raster data with the dimensions in order of
            (band, row, column).

        estimator : estimator object implementing 'fit'
            The object to use to fit the data.

        Returns
        -------
        numpy.ndarray
            3d numpy array representing the multi-target prediction result with
            the dimensions in the order of (target, row, column).
        """
        n_features, rows, cols = img.shape[0], img.shape[1], img.shape[2]

        mask2d = img.mask.any(axis=0)

        # reshape each image block matrix into a 2D matrix
        # first reorder into rows,cols,bands(transpose)
        # then resample into 2D array (rows=sample_n, cols=band_values)
        n_samples = rows * cols
        flat_pixels = img.transpose(1, 2, 0).reshape((n_samples, n_features))

        # predict probabilities
        result = estimator.predict(flat_pixels)

        # reshape class probabilities back to 3D image [iclass, rows, cols]
        result = result.reshape((rows, cols, result.shape[1]))

        # reshape band into rasterio format [band, row, col]
        result = result.transpose(2, 0, 1)

        # repeat mask for n_bands
        mask3d = np.repeat(a=mask2d[np.newaxis, :, :],
                           repeats=result.shape[0], axis=0)

        # convert proba to masked array
        result = np.ma.masked_array(
            result,
            mask=mask3d,
            fill_value=np.nan)

        return result

    def predict(self, estimator, output, height=None, overwrite=False):
        """Prediction method for RasterStack class.

        Parameters
        ----------
        estimator : estimator object implementing 'fit'
            The object to use to fit the data.
            
        output : str
            Output name for prediction raster.
            
        height : int (opt).
            Number of raster rows to pass to estimator at one time. If not
            specified then the entire raster is read into memory.
            
        overwrite : bool (opt). Default is False
            Option to overwrite an existing raster.
        """

        reg = Region()
        func = self._pred_fun

        # determine dtype
        test_window = list(self.row_windows(height=1))[0]
        img = self.read(window=test_window)
        result = func(img, estimator)
        
        try:
            np.finfo(result.dtype)
            mtype = 'FCELL'
            nodata = np.nan
        except:
            mtype = 'CELL'
            nodata = -2147483648    
            
        # determine whether multi-target
        if result.shape[0] > 1:
            n_outputs = result.shape[result.ndim-1]
        else:
            n_outputs = 1

        indexes = np.arange(0, n_outputs)
        
        # chose prediction function
        if len(indexes) == 1:
            func = self._pred_fun
        else:
            func = self._predfun_multioutput
        
        if len(indexes) > 1:
            self._predict_multi(
                estimator, reg, indexes, indexes, height, func, output,
                overwrite)
        else:
            if height is not None:

                with RasterRow(output, mode='w', mtype=mtype, 
                               overwrite=overwrite) as dst:                
                    n_windows = len(
                        [i for i in self.row_windows(height=height)])
                
                    data_gen = (
                        (wi, self.read(window=window))
                        for wi, window in enumerate(
                        self.row_windows(height=height)))
            
                    for wi, arr in data_gen:
                        gs.percent(wi, n_windows, 1)
                        result = func(arr, estimator)
                        result = np.ma.filled(result, nodata)
                            
                        # writing data to GRASS raster row-by-row
                        for i in range(result.shape[1]):
                            newrow = Buffer((reg.cols,), mtype=mtype)
                            newrow[:] = result[0, i, :]
                            dst.put_row(newrow)
                
            else:
                arr = self.read()
                result = func(arr, estimator)
                result = np.ma.filled(result, nodata)
                numpy2raster(result[0, :, :], mtype=mtype, 
                             rastname=output,
                             overwrite=overwrite)

        return None

    def predict_proba(self, estimator, output, class_labels=None,
                      height=None, overwrite=False):
        """Prediction method for RasterStack class.

        Parameters
        ----------
        estimator : estimator object implementing 'fit'
            The object to use to fit the data
            
        output : str
            Output name for prediction raster
            
        class_labels : ndarray (opt)
            1d array containing indices of class labels to subset the
            prediction by. Only probability outputs for these classes will be
            produced.
            
        height : int (opt)
            Number of raster rows to pass to estimator at one time. If not
            specified then the entire raster is read into memory.
            
        overwrite : bool (opt). Default is False
            Option to overwrite an existing raster(s)
        """
        reg = Region()
        func = self._prob_fun

        # use class labels if supplied else output preds as 0,1,2...n
        if class_labels is None:
            test_window = list(self.row_windows(height=1))[0]
            img = self.read(window=test_window)
            result = func(img, estimator)            
            class_labels = range(result.shape[2])
        
        # only output positive class if result is binary
        if len(class_labels) == 2:
            class_labels, indexes = [max(class_labels)], [1]
        else:
            indexes = np.arange(0, len(class_labels), 1)

        # create and open rasters for writing
        self._predict_multi(
            estimator, reg, indexes, class_labels, height, func,
            output, overwrite)
        
        return None
    
    def _predict_multi(self, estimator, region, indexes, class_labels, height,
                       func, output, overwrite):
        
        # create and open rasters for writing if incremental reading
        if height is not None:
            dst = []
            
            for i, label in enumerate(class_labels):
                rastername = output + '_' + str(label)
                dst.append(RasterRow(rastername))
                dst[i].open('w', mtype='FCELL', overwrite=overwrite)

            # create data reader generator
            n_windows = len([i for i in self.row_windows(height=height)])
            
            data_gen = ((wi, self.read(window=window)) 
                for wi, window in enumerate(self.row_windows(height=height)))
    
        # perform prediction
        try:
            if height is not None:
                for wi, arr in data_gen:
                    gs.percent(wi, n_windows, 1)
                    result = func(arr, estimator)
                    result = np.ma.filled(result, np.nan)
    
                    # write multiple features to GRASS GIS rasters
                    for i, arr_index in enumerate(indexes):
                        for row in range(result.shape[1]):
                            newrow = Buffer((region.cols, ), mtype='FCELL')
                            newrow[:] = result[arr_index, row, :]
                            dst[i].put_row(newrow)
            else:
                arr = self.read()
                result = func(arr, estimator)
                result = np.ma.filled(result, np.nan)
                
                for i, arr_index in enumerate(indexes):
                    numpy2raster(result[arr_index, :, :], mtype='FCELL', 
                                 rastname=rastername[i],
                                 overwrite=overwrite)
        except:
            gs.fatal('Error in raster prediction')
        
        finally:
            if height is not None:
                for i in dst:
                    i.close()

    def row_windows(self, region=None, height=25):
        """Returns an generator for row increments, tuple (startrow, endrow).

        Parameters
        ----------
        region : grass.pygrass.gis.region.Region (opt)
            Whether to restrict windows to specified region.
            
        height : int (opt). Default is 25
            Height of window in number of image rows.
        """

        if region is None:
            region = Region()

        windows = ((row, row+height) if row+height <= region.rows else
                   (row, region.rows) for row in range(0, region.rows, height))

        return windows
    
    def extract_pixels(self, response, as_df=False):
        """Extract pixel values from a RasterStack using another RasterRow
        object of labelled pixels
        
        Parameters
        ----------
        response : RasterRow
            RasterRow object containing labelled pixels
        
        as_df : bool (opt). Default is False
            Whether to return the extracted RasterStack pixels as a Pandas
            DataFrame.
        """
        
        data = r.stats(input=[response] + self.names,
                       separator='pipe',
                       flags=['n', 'g'], 
                       stdout_=PIPE).outputs.stdout
                
        data = data.split(os.linesep)[:-1]
        data = [i.split('|') for i in data]
        data = np.asarray(data).astype('float32')

        # remove x,y columns from array indexes 1 and 2
        data = data[:, 2:]
        
        y = data[:, 0]
        X = data[:, 1:]
        
        if (y % 1).all() == 0:
            y = y.astype('int')
        
        cat = np.arange(0, y.shape[0])
        
        if as_df is True:
            data = pd.DataFrame(
                data=np.column_stack((cat, data)), 
                columns=['cat'] + [response] + self.names)
            
            return data
                    
        return X, y, cat

    def extract_points(self, vect_name, fields, na_rm=True, as_df=False):
        """Samples a list of GDAL rasters using a point data set.

        Parameters
        ----------
        vect_name : str
            Name of GRASS GIS vector containing point features.
            
        fields : list, str
            Name of attribute(s) containing the response variable(s).
            
        na_rm : bool (opt). Default is True
            Whether to remove samples containing NaNs.
        
        as_df : bool (opt). Default is False.
            Whether to return the extracted RasterStack values as a Pandas
            DataFrame.

        Returns
        -------
        X : ndarray
            2d array containing the extracted raster values with the dimensions
            ordered by (n_samples, n_features).
            
        y : ndarray
            1d or 2d array of labels with the dimensions ordered by 
            (n_samples, n_fields).
                
        df : pandas.DataFrame
            Extracted raster values as Pandas DataFrame if as_df = True.
        """
                
        if isinstance(fields, str):
            fields = [fields]
                
        # open grass vector
        with VectorTopo(vect_name.split('@')[0], mode='r') as points:
            
            key_col = points.table.key
                
            # read table for all points (irrespective of comp region)
            df = pd.DataFrame(points.table_to_dict()).transpose()
            df_cols = points.table.columns
            df_cols = [name for (name, dtype) in df_cols.items()]
            df = df.rename(
                columns={old: new for old, new in zip(df.columns, df_cols)})
            df = df.loc[:, fields + [points.table.key]]
            
            Xs = []
    
            # extract raster data    
            for name, src in self.loc.items():
                
                # query raster data in comp region
                rast_data = v.what_rast(
                    map=vect_name,
                    raster=src.fullname(),
                    flags='p', quiet=True, stdout_=PIPE).outputs.stdout
                
                rast_data = rast_data.split(os.linesep)[:-1]
                
                X = (np.asarray([k.split('|')[1]
                    if k.split('|')[1] != '*' else np.nan for k in rast_data]))

                cat = (np.asarray([int(k.split('|')[0])
                    if k.split('|')[1] != '*' else 0 for k in rast_data]))
            
                # src.open('r')
                # if src.mtype == 'CELL':
                #     X = [int(i) for i in X]
                # else:
                #     X = [float(i) for i in X]

                X = [float(i) for i in X]
                # src.close()
                
                X = pd.DataFrame(data=np.column_stack((X, cat)), 
                                 columns=[name, key_col])
                Xs.append(X)
        
        for X in Xs:
            df = df.merge(X, on=key_col)
                                    
        # set any grass integer nodata values to NaN
        df = df.replace(self._cell_nodata, np.nan)

        # remove rows with missing response data
        df = df.dropna(subset=fields)
        
        # remove samples containing NaNs
        if na_rm is True:    
            gs.message('Removing samples with NaN values in the ' +
                       'raster feature variables...')
            df = df.dropna()
            
        if as_df is False:
            if len(fields) == 1:
                fields = fields[0]
            
            X = df.loc[:, df.columns.isin(self.loc.keys())].values
            y = df.loc[:, fields].values
            cat = df.loc[:, key_col].values
            return X, y, cat

        return df

    def to_pandas(self):
        """RasterStack to pandas DataFrame.
        
        Returns
        -------
        pandas.DataFrame
        """

        reg = Region()
        arr = self.read()
        
        # generate x and y grid coordinate arrays
        x_range = np.linspace(start=reg.west, stop=reg.east, num=reg.cols)
        y_range = np.linspace(start=reg.south, stop=reg.north, num=reg.rows)
        xs, ys = np.meshgrid(x_range, y_range)

        # flatten 3d data into 2d array (layer, sample)
        arr = arr.reshape((arr.shape[0], arr.shape[1] * arr.shape[2]))
        arr = arr.transpose()
        
        # convert to dataframe
        df = pd.DataFrame(np.column_stack((xs.flatten(), ys.flatten(), arr)),
                          columns=['x', 'y'] + self.names)

        # set nodata values to nan
        for i, col_name in enumerate(self.names):
            df.loc[df[col_name] == self._cell_nodata, col_name] = np.nan
        
        return df

    def head(self):
        """Show the head (first rows, first columns) or tail (last rows, last 
        columns) of the cells of a Raster object.
        """

        window = (1, 10)
        arr = self.read(window=window)

        return arr

    def tail(self):
        """Show the head (first rows, first columns) or tail (last rows, last
        columns) of the cells of a Raster object.
        """

        reg = Region()
        window = (reg.rows-10, reg.rows)
        arr = self.read(window=window)

        return arr
