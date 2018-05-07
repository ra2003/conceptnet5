"""
Implements 'propagation', whereby terms from the full ConceptNet graph are 
assigned vectors from the embeddings produced by retrofitting against the 
reduced graph.
"""

import numpy as np
import pandas as pd
from scipy.sparse import diags
from conceptnet5.builders.reduce_assoc import make_graph
from .sparse_matrix_builder import SparseMatrixBuilder
from .formats import load_hdf, save_hdf


def sharded_propagate(assoc_filename, embedding_filename, 
                      output_filename, nshards=6, iterations=20):
    """
    A wrapper around propagate which reduces memory requirements by 
    splitting the embedding into shards (along the dimensions of the 
    embedding feature space).
    """
    frame_box = [load_hdf(embedding_filename)]
    adjacency_matrix, combined_index = \
        make_adjacency_matrix(assoc_filename, frame_box[0].index)
    shard_width = frame_box[0].shape[1] // nshards

    for i in range(nshards):
        temp_filename = output_filename + '.shard%d' % i
        shard_from = shard_width * i
        shard_to = shard_from + shard_width
        if len(frame_box) == 0:
            frame_box.append(load_hdf(embedding_filename))
        embedding_shard = pd.DataFrame(
            frame_box[0].iloc[:, shard_from:shard_to])

        frame_box.clear()

        propagated = propagate(combined_index, embedding_shard,
                               adjacency_matrix, iterations=iterations)
        save_hdf(propagated, temp_filename)
        del propagated


def make_adjacency_matrix(assoc_filename, embedding_vocab):
    """
    Build a sparse adjacency matrix for the ConceptNet graph presented 
    in the given assoc file, including all terms from the given embedding 
    vocabulary and removing all terms from connected components of the graph 
    that do not overlap that vocabulary.  Also builds an index giving all 
    terms from the resulting joined graph+embedding vocabulary in the 
    order corresponding to the rows and columns of the matrix.  Note that 
    it is guaranteed that the terms from the embedding vocabulary will 
    preceed the remaining terms in that index.
    
    Returns the matrix and index.
    """
    # First eliminate all connected components of the graph that don't
    # overlap the vocabulary of the embedding; we can't do anything with
    # those terms.

    graph = make_graph(assoc_filename, save_edge_list=False)
    component_labels = graph.find_components()
    good_component_labels = set(label for term, label
                                in component_labels.items()
                                if term in embedding_vocab)
    good_concepts = set(term for term, label
                        in component_labels.items()
                        if label in good_component_labels)
    del component_labels, good_component_labels

    new_vocab = pd.Index(good_concepts - set(embedding_vocab))
    good_concepts = embedding_vocab.append(new_vocab)
    good_concepts_map = {term : i for i, term in enumerate(good_concepts)}
    del new_vocab
    
    # Convert the good part of the graph to an adjacency matrix representation.
    
    builder = SparseMatrixBuilder()
    for v,w in graph.edge_set():
        try:
            index0 = good_concepts_map[v]
            index1 = good_concepts_map[w]
            builder[index0, index1] = 1
            builder[index1, index0] = 1
        except KeyError:
            pass # one of v, w wasn't good
    del graph
    
    adjacency_matrix = builder.tocsr(
        shape=(len(good_concepts), len(good_concepts)), dtype=np.int8)

    return adjacency_matrix, good_concepts


def propagate(combined_index, embedding, adjacency_matrix,
              iterations=20):
    """
    For as many terms as possible in the ConceptNet graph whose edges are 
    presented in the given adjacency matrix (with corresponding term labels 
    in the given index), find a vector in the target space of 
    the vector embedding presented in the given embedding file.
    """

    # Propagate the vectors from the embeddings to the remaining
    # terms, following the edges of the graph.

    embedding_dimension = embedding.values.shape[1]
    new_vocab_size = len(combined_index) - embedding.values.shape[0]
    vectors = np.vstack([embedding.values,
                         np.zeros((new_vocab_size, embedding_dimension),
                                  dtype=embedding.values.dtype)])
    
    for iteration in range(iterations):
        zero_indices = (np.abs(vectors).sum(1) == 0)
        if not np.any(zero_indices):
            break
        # Find terms with zero vectors having neighbors with nonzero vectors.
        nonzero_indices = np.logical_not(zero_indices)
        fringe = (adjacency_matrix.dot(nonzero_indices.astype(np.int8)) != 0)
        fringe = np.logical_and(fringe, zero_indices)
        # Then pick a neighbor for each, and use it to update the zero vector.
        adjacent_nonzeros = adjacency_matrix.dot(
            diags([nonzero_indices.astype(np.int8)], [0], format='csc'))
        neighbors = np.argmax(adjacent_nonzeros[fringe, :], axis=1)
        neighbors = neighbors.A[:, 0] # convert matrix to 1D ndarray
        vectors[fringe, :] = vectors[neighbors, :]
    
    result = pd.DataFrame(index=combined_index, data=vectors)
    return result
